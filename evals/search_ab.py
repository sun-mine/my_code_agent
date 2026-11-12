"""Isolated, exploratory A/B evaluation; never registers tools in the application."""
import argparse
import ast
import gzip
import hashlib
import json
import os
import platform
from pathlib import Path
import signal
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

from evals import runner
from evals.hybrid_search import HybridIndex, build_chunks
from evals.ollama_eval_client import OllamaEvalClient, post_json

ROOT = Path(__file__).resolve().parents[1]
TASKS = Path(__file__).with_name("search_ab_tasks.json")
LIMITS = dict(max_rounds=8, max_tool_calls=12, max_seconds=120, max_tokens=20000,
              max_repair_attempts=0)
COMMON_SYSTEM = (
    "The repository root is '.' and tools accept paths relative to it. "
    "Never use placeholder paths such as /path/to/source_code. "
    "Work with the supplied tools: list_files(path='.') discovers files; grep searches text; "
    "lookup_symbol locates Python definitions; read_file reads numbered source. "
    "Inspect evidence and execute tools now instead of ending with a plan. "
    "For localization you MUST call submit_locations once with 1-5 path/symbol/line objects "
    "before ending. Use the exact qualified symbol and line from source. "
    "After submitting, finish with one brief sentence. "
    "If hybrid_search is available it is an optional search method, not a requirement. "
    "For a repair, actually edit existing source files using tools and preserve tests. "
    "Independent tests run after you finish. Do not execute code."
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load(path):
    return json.loads(path.read_text())


def get(base, route):
    with urllib.request.urlopen(base + route, timeout=15) as response:
        return json.load(response)


def validate_paths(files):
    for name in files:
        path = Path(name)
        if not name or path.is_absolute() or "\\" in name or any(
                part in {"", ".", ".."} for part in name.split("/")):
            raise ValueError(f"Invalid snapshot path: {name}")


def scheduled_runs(tasks):
    rows = []
    for kind, key, repeats in (("loc", "localization", 2), ("repair", "repairs", 1)):
        for repeat in range(1, repeats + 1):
            for number, task in enumerate(tasks[key]):
                profiles = ("A", "B") if (number + repeat) % 2 else ("B", "A")
                rows.extend(dict(kind=kind, task_id=task["id"], repeat=repeat, profile=profile, order=order)
                            for order, profile in enumerate(profiles))
    return rows


def workspace_hashes(repo):
    return {path.relative_to(repo).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in repo.rglob("*") if path.is_file() and not any(
                part in {".git", ".agent", "__pycache__"} for part in path.relative_to(repo).parts)}


def score_repair(task, repo, evaluator, original):
    current = workspace_hashes(repo)
    changed = {path for path in original.keys() | current.keys() if original.get(path) != current.get(path)}
    if not changed <= set(task["solution_files"]):
        return {"patch_accepted": False, "test_evidence": {"unauthorized_changes": sorted(changed)},
                "changed_paths": sorted(changed)}
    passed, evidence = runner._score(task, repo, evaluator, runner._file_hashes(repo))
    return {"patch_accepted": passed, "test_evidence": evidence, "changed_paths": sorted(changed),
            "diff": runner._run(["git", "diff"], repo)["stdout"]}


def validate_frozen(output, check_models=True):
    protocol = load(output / "protocol.json")
    tasks = load(output / "tasks.json")
    if digest(tasks) != protocol["task_hash"]:
        raise ValueError("Frozen tasks changed")
    for corpus, files in load(output / "source-snapshot.json").items():
        validate_paths(files)
        if digest(files) != protocol["source_hashes"][corpus]:
            raise ValueError("Frozen source changed")
    fixtures = load(output / "repair-fixtures.json")
    if digest(fixtures) != protocol["repair_fixtures_hash"]:
        raise ValueError("Frozen repair fixtures changed")
    for fixture in fixtures.values():
        validate_paths(fixture["files"])
    if hashlib.sha256((output / "index.json.gz").read_bytes()).hexdigest() != protocol["index_sha256"]:
        raise ValueError("Frozen index changed")
    for name, expected in protocol["implementation_hashes"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Evaluation implementation changed: {name}")
    if check_models:
        if get(protocol["endpoint"], "/api/version") != protocol["ollama"]:
            raise ValueError("Ollama version changed")
        installed = {m["name"]: m["digest"] for m in get(protocol["endpoint"], "/api/tags")["models"]}
        for model in protocol["models"]["models"]:
            if installed.get(model["name"]) != model["digest"]:
                raise ValueError("Model digest changed")
    return protocol, tasks


def repair_fixture(task_id, prompt):
    manifest = runner.load_manifest()
    originals = manifest["tasks"]
    chosen = next(task for task in originals if task["id"] == task_id)
    target = originals.index(chosen)
    module = f"source.unit_{target:02d}"
    task = dict(chosen, prompt=prompt)
    task["files"] = {f"source/unit_{i:02d}.py": item["files"]["exercise.py"]
                     for i, item in enumerate(originals)}
    task["files"]["source/__init__.py"] = ""
    task["files"]["tests/test_public.py"] = chosen["files"]["tests/test_public.py"].replace(
        "from exercise import", f"from {module} import")
    task["acceptance"] = chosen["acceptance"].replace("from exercise import", f"from {module} import")
    task["solution_files"] = {f"source/unit_{target:02d}.py": chosen["solution_files"]["exercise.py"]}
    task["task_hash"] = runner._digest(runner._task_payload(task))
    task["scoring_hash"] = runner._digest(runner._scoring_payload(task))
    return task


def prepare(args):
    output = args.output
    if (output / "protocol.json").exists():
        raise ValueError("Output already prepared; use a new directory to freeze a new experiment")
    if args.endpoint not in {"http://127.0.0.1:11435", "http://127.0.0.1:11434"}:
        raise ValueError("This local experiment accepts only a loopback Ollama endpoint")
    files = {}
    for directory in ("api", "components", "core", "tools", "frontend/src"):
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix in {".py", ".ts", ".tsx"}:
                files[path.relative_to(ROOT).as_posix()] = path.read_text()
    for name in ("agent.py", "api_server.py"):
        files[name] = (ROOT / name).read_text()
    tasks = load(TASKS)
    fixture = repair_fixture(tasks["repairs"][0]["id"], tasks["repairs"][0]["prompt"])
    corpora = {"project": files, "repairs": {p: s for p, s in fixture["files"].items()
                                             if not p.startswith("tests/")}}
    version, models = get(args.endpoint, "/api/version"), get(args.endpoint, "/api/tags")
    names = {model["name"] for model in models["models"]}
    if not {args.model, args.embedding_model} <= names:
        raise ValueError("Required models are not installed; this runner never downloads models")
    # Snapshot and labels live outside the model-visible temporary workspace.
    save(output / "source-snapshot.json", corpora)
    save(output / "tasks.json", tasks)
    save(output / "repair-fixtures.json", {item["id"]: repair_fixture(item["id"], item["prompt"])
                                          for item in tasks["repairs"]})
    index_data, index_metrics = {}, {}
    for corpus, sources in corpora.items():
        chunks = build_chunks(sources)
        vectors, counts, durations = [], [], []
        started = time.monotonic()
        for offset in range(0, len(chunks), 8):
            batch = chunks[offset:offset + 8]
            response = post_json(args.endpoint, "/api/embed", {
                "model": args.embedding_model,
                "input": [f"{c['path']} {c['symbol']}\n{c['text']}" for c in batch],
                "truncate": False, "keep_alive": "5m",
            }, timeout=180)
            vectors.extend(response["embeddings"])
            counts.append(response.get("prompt_eval_count"))
            durations.append({key: response.get(key) for key in ("total_duration", "load_duration")})
            print(f"INDEX {corpus}: {min(offset + 8, len(chunks))}/{len(chunks)}", flush=True)
        index = HybridIndex(chunks, vectors)
        index_data[corpus] = {"chunks": chunks, "vectors": vectors}
        index_metrics[corpus] = {"files": len(sources), "chunks": len(chunks),
            "seconds": time.monotonic() - started, "embedding_tokens": sum(counts) if all(
                count is not None for count in counts) else None,
            "request_metrics": durations, "parameters": index.stats}
    post_json(args.endpoint, "/api/embed", {"model": args.embedding_model, "input": [],
              "keep_alive": 0}, timeout=60)
    with gzip.open(output / "index.json.gz", "wt") as stream:
        json.dump(index_data, stream, ensure_ascii=False)
    source_hashes = {name: digest(sources) for name, sources in corpora.items()}
    save(output / "protocol.json", {"scope": "exploratory, first-party; no overall capability claim",
         "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "limits": LIMITS,
         "model": args.model, "embedding_model": args.embedding_model,
         "endpoint": args.endpoint, "ollama": version, "models": models,
         "runtime": {"python": platform.python_version(), "platform": platform.platform(),
                     "anthropic": __import__("anthropic").__version__},
         "temperature": 0, "seed": 42, "num_ctx": 8192, "max_new_tokens_per_round": 700,
         "localization_repeats": 2, "repair_repeats": 1, "source_hashes": source_hashes,
         "repair_fixtures_hash": digest(load(output / "repair-fixtures.json")),
         "task_hash": digest(tasks), "index": index_metrics,
         "index_sha256": hashlib.sha256((output / "index.json.gz").read_bytes()).hexdigest(),
         "implementation_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in [Path(__file__), TASKS, Path(__file__).with_name("hybrid_search.py"),
                       Path(__file__).with_name("ollama_eval_client.py"), Path(__file__).with_name("runner.py")]
                       + [ROOT / name for name in files if name.endswith(".py")]},
         "query_cache": False, "index_cache": "frozen corpus vectors; cold cost reported separately",
         "embedding_keep_alive": 0, "warmup": "same Qwen warm-up before every run; excluded and recorded",
         "tool_output_limit_chars": 8000, "submission_limit": 5,
         "repair_note": "20 synthetic source modules; four rewritten tasks; not real-repository repairs"})


def symbols(files):
    found = {}
    for path, source in files.items():
        if not path.endswith(".py"):
            continue
        def visit(node, parents=()):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = ".".join((*parents, node.name))
                found[(path, name)] = (node.lineno, node.end_lineno)
                parents = (*parents, node.name)
            for child in ast.iter_child_nodes(node):
                visit(child, parents)
        visit(ast.parse(source))
    return found


def score_locations(task, submissions, definitions):
    locations = submissions[0] if submissions else []
    hits = []
    for path, name in task["targets"]:
        start, end = definitions[(path, name)]
        hit = any(item.get("path") == path and item.get("symbol") in {name, name.split(".")[-1]}
                  and type(item.get("line")) is int and start <= item["line"] <= end for item in locations)
        hits.append(hit)
    return {"recall_at_5": sum(hits) / len(hits), "all_targets_found": all(hits),
            "target_hits": hits, "submitted": locations}


def worker(args):
    from components.background_manager import BackgroundManager
    from components.message_bus import MessageBus
    from components.command_runner import ToolResult
    from core.loop import AgentLoop, RunLimits
    from tools.base import BASE_HANDLERS, BASE_TOOLS, safe_path, set_workdir

    protocol, corpora, tasks = (load(args.output / name) for name in
                                ("protocol.json", "source-snapshot.json", "tasks.json"))
    kind, task_id, profile, repeat = args.worker
    task = next(t for t in tasks["localization" if kind == "loc" else "repairs"] if t["id"] == task_id)
    records, submissions, searches = [], [], []
    with tempfile.TemporaryDirectory(prefix="code-agent-search-ab-") as temporary:
        parent = Path(temporary)
        if kind == "loc":
            repo = parent / "workspace"
            validate_paths(corpora["project"])
            runner._write_files(repo, corpora["project"])
        else:
            task = load(args.output / "repair-fixtures.json")[task_id]
            repo = runner.create_task_repo(task, parent)
        original = workspace_hashes(repo) if kind == "repair" else {}
        set_workdir(repo)
        names = {"read_file", "list_files", "glob", "grep", "lookup_symbol"}
        if kind == "repair":
            names |= {"write_file", "edit_file"}
        tools = [dict(tool) for tool in BASE_TOOLS if tool["name"] in names]
        handlers = {name: BASE_HANDLERS[name] for name in names}

        def read_file(path, start_line=1, limit=80, **_):
            lines = safe_path(path).read_text().splitlines()
            start_line = max(1, int(start_line))
            limit = min(120, max(1, int(limit)))
            return "\n".join(f"{i}: {line}" for i, line in enumerate(
                lines[start_line - 1:start_line - 1 + limit], start_line))

        handlers["read_file"] = read_file
        for name in ("write_file", "edit_file"):
            if name in handlers:
                original_handler = handlers[name]
                def source_only(_handler=original_handler, **kwargs):
                    path = kwargs.get("path", "")
                    if not re.fullmatch(r"source/unit_\d{2}\.py", path) or not safe_path(path).is_file():
                        raise ValueError("Only existing implementation modules may be edited")
                    return _handler(**kwargs)
                handlers[name] = source_only
        read_tool = next(tool for tool in tools if tool["name"] == "read_file")
        read_tool["input_schema"] = {"type": "object", "properties": {
            "path": {"type": "string"}, "start_line": {"type": "integer"},
            "limit": {"type": "integer"}}, "required": ["path"]}
        read_tool["description"] = "Read current source with line numbers; at most 120 lines per call."
        if kind == "loc":
            def submit_locations(locations, **_):
                if submissions:
                    raise ValueError("Only one submission is allowed")
                if not isinstance(locations, list) or not 1 <= len(locations) <= 5:
                    raise ValueError("Submit 1 to 5 locations")
                for location in locations:
                    if not isinstance(location, dict) or not isinstance(location.get("symbol"), str) or type(location.get("line")) is not int:
                        raise ValueError("Each location needs a symbol string and integer line")
                    safe_path(location["path"]).relative_to(repo)
                    if not safe_path(location["path"]).is_file():
                        raise ValueError("File does not exist")
                submissions.append(locations)
                save(args.result.with_suffix(".phase.json"), {"phase": "agent", "started": agent_started,
                     "workspace": str(repo), "original": original, "submissions": submissions})
                return "Submission recorded. Finish your answer briefly."
            tools.append({"name": "submit_locations", "description":
                "Submit your final 1-5 precise code locations once, based on inspected evidence. Then finish.",
                "input_schema": {"type": "object", "properties": {"locations": {"type": "array",
                "items": {"type": "object", "properties": {"path": {"type": "string"},
                "symbol": {"type": "string"}, "line": {"type": "integer"}},
                "required": ["path", "symbol", "line"]}}}, "required": ["locations"]}})
            handlers["submit_locations"] = submit_locations
        if profile == "B":
            with gzip.open(args.output / "index.json.gz", "rt") as stream:
                data = json.load(stream)["project" if kind == "loc" else "repairs"]
            index = HybridIndex(data["chunks"], data["vectors"])

            def hybrid_search(query, **_):
                started = time.monotonic()
                attempt = {"query": query, "success": False, "embedding_tokens": None}
                searches.append(attempt)
                try:
                    remaining = protocol["limits"]["max_seconds"] - (started - agent_started)
                    if remaining <= 0:
                        raise TimeoutError("Agent budget exhausted")
                    response = post_json(protocol["endpoint"], "/api/embed", {
                        "model": protocol["embedding_model"], "input": query, "truncate": False,
                        "keep_alive": protocol["embedding_keep_alive"]}, timeout=min(30, remaining))
                    if response.get("prompt_eval_count") is None:
                        raise ValueError("Embedding usage missing")
                    result = index.search(query, response["embeddings"][0], limit=5)
                    attempt.update(success=True, embedding_tokens=response["prompt_eval_count"],
                        embedding_load_duration=response.get("load_duration"), results=result)
                    displayed = [dict(hit, text=hit["text"][:700], excerpt_truncated=len(hit["text"]) > 700)
                                 for hit in result]
                    return json.dumps(displayed, ensure_ascii=False)
                except Exception as exc:
                    attempt["error"] = f"{type(exc).__name__}: {exc}"
                    raise
                finally:
                    attempt["seconds"] = time.monotonic() - started
            tools.append({"name": "hybrid_search", "description":
                "Search code using natural language or keywords with BM25 + semantic embeddings + RRF. Returns top 5 code spans.",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                                 "required": ["query"]}})
            handlers["hybrid_search"] = hybrid_search
        # No shell or execution tool: the agent cannot inspect evaluator files via code execution.
        # All filesystem handlers resolve paths inside its source-only workspace.
        def bounded(handler):
            def call(**kwargs):
                try:
                    result = handler(**kwargs)
                    if isinstance(result, ToolResult):
                        return ToolResult(str(result)[:8000], is_error=result.is_error)
                    return str(result)[:8000]
                except Exception as exc:
                    return ToolResult(f"Error: {exc}", is_error=True)
            return call
        handlers = {name: bounded(handler) for name, handler in handlers.items()}
        events = []
        client = OllamaEvalClient(protocol["endpoint"], protocol["model"], timeout=LIMITS["max_seconds"],
                                  seed=protocol["seed"], records=records)
        agent = AgentLoop(client=client, model=protocol["model"], system=COMMON_SYSTEM,
            tools=tools, tool_handlers=handlers, bg=BackgroundManager(repo),
            bus=MessageBus(repo / ".agent/inbox"), limits=RunLimits(**protocol["limits"]),
            action_continuation=False, event_callback=events.append)
        started = agent_started = time.monotonic()
        save(args.result.with_suffix(".phase.json"), {"phase": "agent", "started": started,
             "workspace": str(repo), "original": original, "submissions": submissions})
        answer = agent.run([{"role": "user", "content": task["prompt"]}])
        seconds = time.monotonic() - started
        save(args.result.with_suffix(".phase.json"), {"phase": "score", "started": time.monotonic()})
        end = next((event for event in reversed(events) if event.get("type") == "run_end"), {})
        score = score_locations(task, submissions, symbols(corpora["project"])) if kind == "loc" else {}
        if kind == "repair":
            evaluator = parent / "evaluator"
            evaluator.mkdir()
            score = score_repair(task, repo, evaluator, original)
        usage_complete = bool(records) and all(record.get("prompt_eval_count") is not None
            and record.get("eval_count") is not None for record in records)
        known_inputs = sum(record.get("prompt_eval_count") or 0 for record in records)
        known_outputs = sum(record.get("eval_count") or 0 for record in records)
        timeout = end.get("stop_reason") == "max_seconds"
        infra = None if timeout else ("provider_or_retrieval_error" if
            end.get("stop_reason") == "error" or not usage_complete or any(
                not search["success"] for search in searches) else None)
        save(args.result, {"kind": kind, "task_id": task_id, "profile": profile,
            "protocol_hash": digest(protocol),
            "repeat": int(repeat), "agent_seconds": seconds, "outcome": end,
            "llm_input_tokens": known_inputs if usage_complete else None,
            "llm_output_tokens": known_outputs if usage_complete else None,
            "known_llm_tokens": known_inputs + known_outputs, "usage_complete": usage_complete,
            "embedding_usage_complete": all(search.get("embedding_tokens") is not None for search in searches),
            "generation_requests": records, "hybrid_searches": searches,
            "available_tools": [tool["name"] for tool in tools], "answer": answer,
            "events": events, "score": score, "infrastructure_error": infra})


def valid_pairs(rows, schedule, protocol_hash):
    expected = {(r["kind"], r["task_id"], r["repeat"], r["profile"]) for r in schedule}
    indexed = {}
    for row in rows:
        key = (row["kind"], row["task_id"], row["repeat"], row["profile"])
        if key not in expected or key in indexed or row.get("protocol_hash") != protocol_hash:
            raise ValueError("Unexpected, duplicate or mixed-protocol result")
        indexed[key] = row
    valid = []
    for kind, task_id, repeat in sorted({key[:3] for key in expected}):
        pair = [indexed.get((kind, task_id, repeat, profile)) for profile in ("A", "B")]
        if all(row is not None and not row.get("infrastructure_error")
               and not row.get("budget_overrun") for row in pair):
            valid.extend(pair)
    return {"rows": valid, "missing": len(expected - indexed.keys()),
            "infra": sum(bool(r.get("infrastructure_error") or r.get("budget_overrun")) for r in rows),
            "complete": len(indexed) == len(expected), "scheduled": len(expected)}


def run(args):
    output = args.output
    protocol, tasks = validate_frozen(output)
    plan = scheduled_runs(tasks)
    if args.task:
        plan = [item for item in plan if item["task_id"] in args.task]
    for item in plan:
        kind, task_id, repeat, profile = (item[key] for key in ("kind", "task_id", "repeat", "profile"))
        name = f"{task_id}-r{repeat}-{profile}"
        result_path = output / "runs" / f"{name}.json"
        phase_path = result_path.with_suffix(".phase.json")
        if result_path.exists():
            continue
        print(f"START {name}", flush=True)
        resident = get(protocol["endpoint"], "/api/ps")
        if any(model["name"] == protocol["embedding_model"] for model in resident["models"]):
            post_json(protocol["endpoint"], "/api/embed", {"model": protocol["embedding_model"],
                      "input": [], "keep_alive": 0}, timeout=60)
        warmup = post_json(protocol["endpoint"], "/api/chat", {"model": protocol["model"],
            "messages": [{"role": "user", "content": "Reply OK."}], "stream": False,
            "options": {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 1},
            "keep_alive": "5m"}, timeout=90)
        save(output / "warmups" / f"{name}.json", {"response": warmup,
            "residency": get(protocol["endpoint"], "/api/ps")})
        result_path.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "evals.search_ab", "--output", str(output), "--live",
                   "--worker", kind, task_id, profile, str(repeat), "--result", str(result_path)]
        started, hard_timeout, phase, agent_elapsed = time.monotonic(), False, {}, None
        with result_path.with_suffix(".log").open("w") as log:
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            while process.poll() is None:
                if phase_path.exists():
                    try:
                        phase = load(phase_path)
                    except json.JSONDecodeError:
                        pass
                stage_limit = protocol["limits"]["max_seconds"] + 2 if phase.get("phase") == "agent" else 60
                if time.monotonic() - phase.get("started", started) > stage_limit:
                    hard_timeout = phase.get("phase") == "agent"
                    agent_elapsed = time.monotonic() - phase.get("started", started)
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                    break
                time.sleep(0.2)
        if not result_path.exists():
            score = {}
            if hard_timeout and kind == "loc":
                task = next(t for t in tasks["localization"] if t["id"] == task_id)
                score = score_locations(task, phase.get("submissions", []),
                                        symbols(load(output / "source-snapshot.json")["project"]))
            elif hard_timeout and kind == "repair":
                task = load(output / "repair-fixtures.json")[task_id]
                repo = Path(phase["workspace"])
                evaluator = repo.parent / "timeout-evaluator"
                evaluator.mkdir(exist_ok=True)
                score = score_repair(task, repo, evaluator, phase["original"])
            save(result_path, {**item, "protocol_hash": digest(protocol),
                "agent_seconds": agent_elapsed if hard_timeout else None,
                "hard_timeout": hard_timeout, "score": score, "usage_complete": False,
                "embedding_usage_complete": False,
                "outcome": {"stop_reason": "max_seconds" if hard_timeout else "worker_error"},
                "infrastructure_error": None if hard_timeout else "worker_failed",
                "worker_exit_code": process.returncode})
        row = load(result_path)
        row["worker_wall_seconds"] = time.monotonic() - started
        row["order"] = item["order"]
        row["budget_overrun"] = not row.get("hard_timeout") and row.get("agent_seconds") is not None and row["agent_seconds"] > protocol["limits"]["max_seconds"] + 2
        save(result_path, row)
        print(f"DONE {name}: recall={row['score'].get('recall_at_5')} "
              f"patch={row['score'].get('patch_accepted')} "
              f"error={row.get('infrastructure_error')} ({row['worker_wall_seconds']:.1f}s)", flush=True)
    report(args)


def report(args):
    protocol, tasks = load(args.output / "protocol.json"), load(args.output / "tasks.json")
    rows = [load(path) for path in sorted((args.output / "runs").glob("*-r[12]-[AB].json"))]
    pairs = valid_pairs(rows, scheduled_runs(tasks), digest(protocol))
    (args.output / "results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    state = "已完成" if pairs["complete"] else "未完成 INCOMPLETE"
    lines = [f"# Agentic Search 与混合检索 A/B 实测（{state}）", "", "这是自建小样本探索性评测，不是外部基准，不代表总体能力。", "",
        f"计划 {pairs['scheduled']} 次，已有 {len(rows)} 次，缺失 {pairs['missing']} 次，完整有效配对 {len(pairs['rows']) // 2} 对；主表只统计完整有效配对。", "",
        "A：同一 AgentLoop，文件查找、关键词搜索、AST 定义定位和按行读取。",
        "B：保留 A 的工具和循环，增加 BM25＋Embedding＋RRF 检索工具。", "",
        f"生成模型：`{protocol['model']}`；向量模型：`{protocol['embedding_model']}`。",
        f"每次运行：{protocol['limits']}；温度 0，seed 42，上下文 8192，每轮最多生成 700 token。", "",
        "任务：6 道当前项目源码定位题（含精确符号、语义改写、生命周期，各 2 题），每题正反顺序各运行一次；4 道多文件合成修复题，各运行一次。",
        "定位题使用单次、最多 5 个带符号和行号的结构化提交；标注不进入模型工作区。精确符号题是 AST 检查组，不与语义题混称。",
        "修复题每题有 20 个实现模块，测试保护且由评测器在 Agent 结束后执行。修复得分不是实际项目修复能力。", "",
        "修复题可见公开测试中的 import 含定位线索，因此修复通过率用于检查编辑和验收链路，不能单独归因于检索增益。", "",
        "| 指标 | A | B |", "|---|---:|---:|"]
    for kind, metric, label in (("loc", "all_targets_found", "定位全部必要目标"),
                                ("loc", "recall_at_5", "定位平均 Recall@5"),
                                ("repair", "patch_accepted", "修复测试通过")):
        values = []
        for profile in ("A", "B"):
            selected = [r for r in pairs["rows"] if r["kind"] == kind and r["profile"] == profile]
            valid = [r for r in selected if not r.get("infrastructure_error")]
            total = sum(r["score"].get(metric, 0) for r in valid)
            values.append(f"{total / len(valid):.1%}（{len(valid)} 次有效运行）" if valid else "待完成")
        lines.append(f"| {label} | {' | '.join(values)} |")
    for label, getter in (("平均 Agent 耗时（秒）", lambda r: r.get("agent_seconds")),
        ("平均生成模型 Token（输入＋输出，计数完整的运行）", lambda r:
         r["llm_input_tokens"] + r["llm_output_tokens"] if r.get("usage_complete") else None),
        ("平均工具调用次数", lambda r: r.get("outcome", {}).get("tool_calls")),
        ("平均查询向量 Token", lambda r: None if not r.get("embedding_usage_complete", True) or any(s.get("embedding_tokens") is None for s in
         r.get("hybrid_searches", [])) else sum(s["embedding_tokens"] for s in r.get("hybrid_searches", [])))):
        values = []
        for profile in ("A", "B"):
            valid = [r for r in pairs["rows"] if r["profile"] == profile]
            numbers = [getter(r) for r in valid if getter(r) is not None]
            values.append(f"{sum(numbers) / len(numbers):.1f}" if numbers else "待完成")
        lines.append(f"| {label} | {' | '.join(values)} |")
    lines += ["", "## 逐题结果", "", "| 任务 | 次数 | 组别 | 得分 | 秒 | 生成 Token | 混合检索调用 |", "|---|---:|---|---|---:|---:|---:|"]
    for row in rows:
        score = row["score"]
        result = row.get("infrastructure_error") or (f"Recall={score.get('recall_at_5', 0):.0%}" if row["kind"] == "loc" else str(score.get("patch_accepted", False)))
        tokens = row["llm_input_tokens"] + row["llm_output_tokens"] if row.get("usage_complete") else "未知"
        lines.append(f"| {row['task_id']} | {row['repeat']} | {row['profile']} | {result} | {row.get('agent_seconds') or 0:.1f} | {tokens} | {len(row.get('hybrid_searches', []))} |")
    lines += ["", "## 成本与解释边界", "",
        "- 索引预先构建，冷构建耗时及语料向量 Token 单独列出，不计入每次 Agent 耗时。",
        "- B 每次查询都真实生成向量，不缓存查询；向量请求、模型换入换出均计入任务耗时。8GB 主机上向量请求完成后卸载向量模型。",
        "- 每次任务前执行相同生成模型预热，预热不计入任务耗时，原始记录保存在 warmups/。两组串行运行。",
        "- 生成模型 Token 与向量模型 Token 分开统计；B 检索结果进入上下文后正常计入生成模型输入。",
        "- 未调用混合检索的 B 组仍纳入主结果；没有使用增强工具不能解释为增强检索效果。",
        "- 超预算、缺失 usage、进程异常均保留原始记录；基础设施异常另列，不能静默当作零 Token。",
        "- Agent 没有 shell 或代码执行工具；只读定位阶段无法通过工具读取工作区外文件。测试执行属于进程隔离，不声明为安全沙箱。",
        "- 当前源码包含未提交改动，以 source-snapshot.json 和协议里的内容哈希复现，不能只用 Git HEAD。", ""]
    for corpus, data in protocol["index"].items():
        lines.append(f"索引 `{corpus}`：{data['files']} 文件，{data['chunks']} 块，冷构建 {data['seconds']:.2f} 秒，向量 Token {data['embedding_tokens']}。")
    lines += ["", "基础设施异常：" + str(sum(bool(row.get("infrastructure_error")) for row in rows)),
              "超出声明预算：" + str(sum(bool(row.get("budget_overrun")) for row in rows)), ""]
    (args.output / "comparison.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", choices=("prepare", "run", "report"), default="report")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11435")
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--embedding-model", default="bge-m3:latest")
    parser.add_argument("--task", action="append")
    parser.add_argument("--worker", nargs=4)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    args.output = args.output.resolve()
    if (args.worker or args.stage in {"prepare", "run"}) and not args.live:
        parser.error("Network/model calls require --live")
    if args.worker:
        worker(args)
    elif args.stage in {"prepare", "run"}:
        if not args.live:
            parser.error("Network/model calls require --live")
        (prepare if args.stage == "prepare" else run)(args)
    else:
        report(args)


if __name__ == "__main__":
    main()
