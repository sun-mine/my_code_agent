"""Run the frozen first-party repair tasks without silently contacting a model."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


EVAL_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = EVAL_DIR / "manifest.json"
PUBLIC_COMMAND = "python3 -m unittest discover -s tests -q"
PROFILE_NAMES = ("baseline", "retrieval", "repair")


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _task_payload(task: dict) -> dict:
    return {
        key: task[key]
        for key in ("id", "split", "prompt", "files", "public_test_command")
    }


def _scoring_payload(task: dict) -> dict:
    return {"id": task["id"], "acceptance": task["acceptance"]}


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    manifest = json.loads(path.read_text())
    if manifest.get("benchmark") != "code-agent-first-party-repairs":
        raise ValueError("unexpected benchmark name")
    tasks = manifest.get("tasks", [])
    if len(tasks) != 20 or len({task["id"] for task in tasks}) != 20:
        raise ValueError("manifest must contain 20 uniquely named tasks")
    if {task["split"] for task in tasks} != {"dev", "heldout"}:
        raise ValueError("manifest must contain fixed dev and heldout splits")
    for task in tasks:
        if task["public_test_command"] != PUBLIC_COMMAND:
            raise ValueError(f"{task['id']}: public test command changed")
        if task["task_hash"] != _digest(_task_payload(task)):
            raise ValueError(f"{task['id']}: task hash mismatch")
        if task["scoring_hash"] != _digest(_scoring_payload(task)):
            raise ValueError(f"{task['id']}: scoring hash mismatch")
    locked = {key: manifest[key] for key in ("benchmark", "version", "tasks")}
    if manifest.get("manifest_hash") != _digest(locked):
        raise ValueError("manifest hash mismatch")
    return manifest


def _run(command: list[str], cwd: Path, timeout: float = 30) -> dict:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=cwd, text=True, capture_output=True, timeout=timeout,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return {
            "exit_code": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 6),
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": None,
            "duration_seconds": round(time.monotonic() - started, 6),
            "stdout": (exc.stdout or "")[-4000:],
            "stderr": (exc.stderr or "")[-4000:],
            "timed_out": True,
        }


def _write_files(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _file_hashes(root: Path, prefix: str = "tests") -> dict[str, str]:
    base = root / prefix
    if not base.exists():
        return {}
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(base.rglob("*")) if path.is_file()
    }


def create_task_repo(task: dict, parent: Path) -> Path:
    repo = parent / "repo"
    repo.mkdir()
    _write_files(repo, task["files"])
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "eval@example.invalid"],
        ["git", "config", "user.name", "Agent Eval"],
        ["git", "add", "."],
        ["git", "commit", "-q", "-m", "frozen task"],
    ):
        result = _run(command, repo)
        if result["exit_code"] != 0:
            raise RuntimeError(f"task repository setup failed: {result['stderr']}")
    initial = _run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], repo)
    if initial["exit_code"] == 0:
        raise RuntimeError(f"{task['id']}: initial public tests unexpectedly pass")
    return repo


def load_repo_manifest(path: Path) -> dict:
    """Resolve a local-repository task declaration without exposing scorer files."""
    path = path.resolve()
    raw = json.loads(path.read_text())
    tasks = []
    for spec in raw.get("tasks", []):
        required = {
            "id", "repo_path", "base_sha", "issue", "public_test_command",
            "acceptance_script", "protected_paths",
        }
        missing = required - spec.keys()
        if missing:
            raise ValueError(f"{spec.get('id', 'repo task')}: missing {sorted(missing)}")
        if not re.fullmatch(r"[0-9a-f]{40}", spec["base_sha"]):
            raise ValueError(f"{spec['id']}: base_sha must be a full lowercase commit id")
        repo_path = (path.parent / spec["repo_path"]).resolve()
        acceptance_path = (path.parent / spec["acceptance_script"]).resolve()
        if not spec["protected_paths"] or not all(isinstance(item, str) for item in spec["protected_paths"]):
            raise ValueError(f"{spec['id']}: protected_paths must be a non-empty string list")
        try:
            acceptance_path.relative_to(repo_path)
        except ValueError:
            pass
        else:
            raise ValueError(f"{spec['id']}: acceptance_script must be outside repo_path")
        acceptance = acceptance_path.read_bytes()
        if not acceptance.startswith(b"#!"):
            raise ValueError(f"{spec['id']}: acceptance_script must be executable text with a shebang")
        oracle_path = spec.get("oracle_patch")
        oracle = (path.parent / oracle_path).resolve().read_bytes() if oracle_path else None
        timeout = float(spec.get("timeout_seconds", 120))
        if timeout <= 0:
            raise ValueError(f"{spec['id']}: timeout_seconds must be positive")
        visible = {
            "id": spec["id"],
            "base_sha": spec["base_sha"],
            "issue": spec["issue"],
            "public_test_command": spec["public_test_command"],
            "protected_paths": spec["protected_paths"],
            "require_public_failure": bool(spec.get("require_public_failure", False)),
            "timeout_seconds": timeout,
        }
        tasks.append({
            **visible,
            "prompt": spec["issue"],
            "split": "repo",
            "repo_path": repo_path,
            "acceptance_bytes": acceptance,
            "oracle_patch_bytes": oracle,
            "oracle_hash": hashlib.sha256(oracle).hexdigest() if oracle else None,
            "timeout_seconds": timeout,
            "task_hash": _digest(visible),
            "scoring_hash": hashlib.sha256(acceptance).hexdigest(),
        })
    if not tasks or len({task["id"] for task in tasks}) != len(tasks):
        raise ValueError("repo manifest needs at least one uniquely named task")
    version = str(raw.get("version", "1"))
    locked = [{
        "task_hash": task["task_hash"],
        "scoring_hash": task["scoring_hash"],
        "oracle_hash": task["oracle_hash"],
    } for task in tasks]
    return {
        "benchmark": "code-agent-local-repository-tasks",
        "version": version,
        "manifest_hash": _digest({"version": version, "tasks": locked}),
        "tasks": tasks,
    }


def _run_shell(command: str, cwd: Path, timeout: float) -> dict:
    return _run(["/bin/sh", "-lc", command], cwd, timeout)


def _protected_hashes(repo: Path, paths: list[str]) -> dict[str, str]:
    repo = repo.resolve()
    hashes = {}
    for relative in paths:
        target = (repo / relative).resolve()
        try:
            target.relative_to(repo)
        except ValueError as exc:
            raise ValueError(f"protected path escapes repository: {relative}") from exc
        if not target.exists():
            raise ValueError(f"protected path does not exist: {relative}")
        files = [target] if target.is_file() else sorted(target.rglob("*"))
        for item in files:
            if item.is_file():
                hashes[str(item.relative_to(repo))] = hashlib.sha256(item.read_bytes()).hexdigest()
    return hashes


def create_repo_task(task: dict, parent: Path) -> Path:
    source = task["repo_path"]
    if not (source / ".git").exists():
        raise ValueError(f"not a local Git repository: {source}")
    commit = _run(["git", "rev-parse", f"{task['base_sha']}^{{commit}}"], source)
    if commit["exit_code"] != 0 or commit["stdout"].strip() != task["base_sha"]:
        raise ValueError(f"base commit is unavailable: {task['base_sha']}")
    tracked = _run(["git", "ls-tree", "-r", "--name-only", task["base_sha"]], source)
    if tracked["exit_code"] != 0:
        raise RuntimeError(f"cannot inspect base tree: {tracked['stderr']}")
    sensitive = [
        name for name in tracked["stdout"].splitlines()
        if Path(name).name == ".env"
        or (Path(name).name.startswith(".env.") and Path(name).name != ".env.example")
    ]
    if sensitive:
        raise ValueError(f"base commit contains environment files: {', '.join(sensitive)}")
    repo = (parent / "repo").resolve()
    cloned = _run(["git", "clone", "--no-hardlinks", "-q", str(source), str(repo)], parent)
    if cloned["exit_code"] != 0:
        raise RuntimeError(f"temporary clone failed: {cloned['stderr']}")
    checked_out = _run(["git", "checkout", "-q", "--detach", task["base_sha"]], repo)
    if checked_out["exit_code"] != 0:
        raise RuntimeError(f"base checkout failed: {checked_out['stderr']}")
    initial = _run_shell(task["public_test_command"], repo, task["timeout_seconds"])
    if task["require_public_failure"] and initial["exit_code"] == 0:
        raise RuntimeError(f"{task['id']}: initial public test unexpectedly passes")
    return repo


def _run_frozen_test(source: str, repo: Path, evaluator: Path, name: str) -> dict:
    test_path = evaluator / name
    test_path.write_text(source)
    bootstrap = (
        "import runpy,sys;"
        f"sys.path.insert(0,{str(repo)!r});"
        f"runpy.run_path({str(test_path)!r},run_name='__main__')"
    )
    return _run([sys.executable, "-I", "-c", bootstrap], repo)


def _score(task: dict, repo: Path, evaluator: Path, original_tests: dict) -> tuple[bool, dict]:
    tests_unchanged = _file_hashes(repo) == original_tests
    public_source = task["files"]["tests/test_public.py"]
    public = _run_frozen_test(public_source, repo, evaluator, "public_frozen.py")
    acceptance = _run_frozen_test(task["acceptance"], repo, evaluator, "acceptance.py")
    passed = tests_unchanged and public["exit_code"] == 0 and acceptance["exit_code"] == 0
    return passed, {
        "tests_unchanged": tests_unchanged,
        "public": public,
        "acceptance": acceptance,
    }


def _offline(task: dict, repo: Path) -> dict:
    started = time.monotonic()
    _write_files(repo, task["solution_files"])
    return {
        "mode": "offline_oracle",
        "profile": "harness-oracle",
        "agent_outcome": None,
        "agent_stop_reason": None,
        "agent_error": None,
        "agent_latency_seconds": None,
        "tool_calls": None,
        "agent_tool_calls": None,
        "repair_attempts": None,
        "token_usage": None,
        "human_intervention_count": 0,
        "model": None,
        "endpoint": None,
        "replay_actions": [
            *[f"write:{path}" for path in sorted(task["solution_files"])],
            "external:frozen-public-and-acceptance-tests",
        ],
        "replay_latency_seconds": round(time.monotonic() - started, 6),
    }


def _offline_repo(task: dict, repo: Path, evaluator: Path) -> dict:
    if task["oracle_patch_bytes"] is None:
        raise ValueError(f"{task['id']}: offline repo replay requires oracle_patch")
    patch = evaluator / "oracle.patch"
    patch.write_bytes(task["oracle_patch_bytes"])
    started = time.monotonic()
    applied = _run(["git", "apply", "--whitespace=nowarn", str(patch)], repo)
    if applied["exit_code"] != 0:
        raise RuntimeError(f"oracle patch failed: {applied['stderr']}")
    return {
        "mode": "offline_repo_oracle",
        "profile": "harness-oracle",
        "agent_outcome": None,
        "agent_stop_reason": None,
        "agent_error": None,
        "agent_latency_seconds": None,
        "tool_calls": None,
        "agent_tool_calls": None,
        "repair_attempts": None,
        "token_usage": None,
        "human_intervention_count": 0,
        "model": None,
        "endpoint": None,
        "replay_actions": ["git-apply:oracle.patch", "external:public-and-acceptance-tests"],
        "replay_latency_seconds": round(time.monotonic() - started, 6),
    }


def _score_repo(task: dict, repo: Path, evaluator: Path, protected: dict) -> tuple[bool, dict]:
    protected_unchanged = _protected_hashes(repo, task["protected_paths"]) == protected
    public = _run_shell(task["public_test_command"], repo, task["timeout_seconds"])
    acceptance_path = evaluator / "acceptance"
    acceptance_path.write_bytes(task["acceptance_bytes"])
    acceptance_path.chmod(0o700)
    acceptance = _run([str(acceptance_path), str(repo)], evaluator, task["timeout_seconds"])
    passed = protected_unchanged and public["exit_code"] == 0 and acceptance["exit_code"] == 0
    return passed, {
        "protected_paths_unchanged": protected_unchanged,
        "public": public,
        "acceptance": acceptance,
    }


def _minimal_tools(profile: str) -> tuple[list[dict], dict]:
    from tools.base import BASE_HANDLERS, BASE_TOOLS

    names = {"read_file", "write_file", "edit_file", "bash"}
    if profile in {"retrieval", "repair"}:
        names.add("lookup_symbol")
    definitions = [tool for tool in BASE_TOOLS if tool["name"] in names]
    handlers = {name: BASE_HANDLERS[name] for name in names}
    return definitions, handlers


def _model_config() -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    model = os.environ.get("MODEL") or os.environ.get("MODEL_ID") or os.environ.get("ANTHROPIC_MODEL")
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    if not key or not model:
        raise RuntimeError(
            "--live requires ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) and "
            "MODEL (or MODEL_ID/ANTHROPIC_MODEL)"
        )
    return {"api_key": key, "model": model, "base_url": base_url}


def _safe_endpoint(value: str | None) -> str | None:
    if not value:
        return None
    parts = urlsplit(value)
    if not parts.scheme or not parts.hostname:
        return "custom endpoint (redacted)"
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, "", "", ""))


def _scrub_model_credentials() -> None:
    # Repository tests and Agent tools run as subprocesses and inherit os.environ.
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)


def _live(task: dict, repo: Path, profile: str, limits: dict, config: dict) -> dict:
    from anthropic import Anthropic
    from components.background_manager import BackgroundManager
    from components.message_bus import MessageBus
    from core.loop import AgentLoop, RunLimits
    from tools.base import set_workdir, run_list_files

    tools, handlers = _minimal_tools(profile)
    events: list[dict] = []
    set_workdir(repo)
    client_args = {"api_key": config["api_key"], "timeout": limits["max_seconds"], "max_retries": 0}
    if config["base_url"]:
        client_args["base_url"] = config["base_url"]
    client = Anthropic(**client_args)
    _scrub_model_credentials()
    run_limits = RunLimits(**limits)
    verification = task["public_test_command"] if profile == "repair" else None
    agent = AgentLoop(
        client=client,
        model=config["model"],
        system=(
            "You are being evaluated on a small Python bug fix. Work only in the current "
            "repository. Inspect the implementation, make the smallest correct source change, "
            "preserve all tests, and run the public tests. Do not ask for help. "
            "Use tools to modify existing source files; a code snippet in your answer does not change a file. "
            "Do not guess new file paths. Inspect the files listed below first.\n\n"
            f"Workspace files (bounded initial context):\n{run_list_files('.', 2)[:6000]}"
        ),
        tools=tools,
        tool_handlers=handlers,
        bg=BackgroundManager(repo),
        bus=MessageBus(repo / ".agent" / "inbox"),
        limits=run_limits,
        verification_command=verification,
        action_continuation=False,
        event_callback=events.append,
    )
    messages = [{"role": "user", "content": task["prompt"]}]
    started = time.monotonic()
    agent.run(messages, run_id=f"eval-{task['id']}")
    latency = round(time.monotonic() - started, 6)
    run_end = next((event for event in reversed(events) if event.get("type") == "run_end"), {})
    error_event = next((event for event in reversed(events) if event.get("type") == "error"), {})
    usage = [event for event in events if event.get("type") == "usage"]
    token_usage = None
    if usage:
        inputs = sum(event.get("input_tokens", 0) for event in usage)
        outputs = sum(event.get("output_tokens", 0) for event in usage)
        token_usage = {"input_tokens": inputs, "output_tokens": outputs, "total_tokens": inputs + outputs}
    starts = [event for event in events if event.get("type") == "tool_start"]
    return {
        "mode": "live",
        "profile": profile,
        "agent_outcome": run_end.get("outcome", "failed"),
        "agent_stop_reason": run_end.get("stop_reason", "missing_run_end"),
        "agent_error": error_event.get("message"),
        "agent_latency_seconds": latency,
        "tool_calls": run_end.get("tool_calls"),
        "agent_tool_calls": sum(event.get("purpose") == "agent" for event in starts),
        "repair_attempts": run_end.get("repair_attempts", 0),
        "token_usage": token_usage,
        "human_intervention_count": 0,
        "model": config["model"],
        "endpoint": _safe_endpoint(config["base_url"]),
        "budget": dict(limits),
        "available_tools": [tool["name"] for tool in tools],
    }


def run_task(task: dict, *, live: bool, profile: str, limits: dict, config: dict | None) -> dict:
    started = time.monotonic()
    base = {
        "task_id": task["id"],
        "split": task["split"],
        "task_hash": task["task_hash"],
        "scoring_hash": task["scoring_hash"],
        "agent_outcome": None,
        "agent_stop_reason": None,
        "agent_error": None,
        "agent_latency_seconds": None,
        "tool_calls": None,
        "agent_tool_calls": None,
        "repair_attempts": None,
        "token_usage": None,
        "human_intervention_count": 0,
        "model": config["model"] if live and config else None,
        "endpoint": _safe_endpoint(config["base_url"]) if live and config else None,
        "budget": dict(limits) if live else None,
        "error": None,
    }
    try:
        if live:
            _scrub_model_credentials()
        with tempfile.TemporaryDirectory(prefix=f"agent-eval-{task['id']}-") as temp:
            temp_root = Path(temp)
            repo = create_task_repo(task, temp_root)
            original_tests = _file_hashes(repo)
            run_data = _live(task, repo, profile, limits, config) if live else _offline(task, repo)
            evaluator = temp_root / "evaluator"
            evaluator.mkdir()
            passed, evidence = _score(task, repo, evaluator, original_tests)
            base.update(run_data)
            base.update({
                "status": "passed" if passed else "failed",
                "completion": passed if live else None,
                "harness_pass": passed if not live else None,
                "scoring": evidence,
            })
            if not passed:
                if not evidence["tests_unchanged"]:
                    base["error"] = "public_tests_modified"
                elif evidence["public"]["exit_code"] != 0:
                    base["error"] = "frozen_public_tests_failed"
                else:
                    base["error"] = "acceptance_tests_failed"
    except Exception as exc:
        base.update({
            "mode": "live" if live else "offline_oracle",
            "profile": profile if live else "harness-oracle",
            "status": "error",
            "completion": False if live else None,
            "harness_pass": False if not live else None,
            "error": f"{type(exc).__name__}: {exc}",
            "human_intervention_count": 0,
        })
    base["total_latency_seconds"] = round(time.monotonic() - started, 6)
    return base


def run_repo_task(task: dict, *, live: bool, profile: str, limits: dict, config: dict | None) -> dict:
    """Run a declared task in a disposable clone, leaving its source repository untouched."""
    started = time.monotonic()
    base = {
        "task_id": task["id"],
        "split": "repo",
        "task_hash": task["task_hash"],
        "scoring_hash": task["scoring_hash"],
        "base_sha": task["base_sha"],
        "oracle_hash": task["oracle_hash"],
        "agent_outcome": None,
        "agent_stop_reason": None,
        "agent_error": None,
        "agent_latency_seconds": None,
        "tool_calls": None,
        "agent_tool_calls": None,
        "repair_attempts": None,
        "token_usage": None,
        "human_intervention_count": 0,
        "model": config["model"] if live and config else None,
        "endpoint": _safe_endpoint(config["base_url"]) if live and config else None,
        "budget": dict(limits) if live else None,
        "error": None,
    }
    try:
        if live:
            _scrub_model_credentials()
        with tempfile.TemporaryDirectory(prefix=f"agent-repo-eval-{task['id']}-") as temp:
            temp_root = Path(temp)
            repo = create_repo_task(task, temp_root)
            protected = _protected_hashes(repo, task["protected_paths"])
            evaluator = temp_root / "evaluator"
            evaluator.mkdir()
            run_data = (
                _live(task, repo, profile, limits, config)
                if live else _offline_repo(task, repo, evaluator)
            )
            passed, evidence = _score_repo(task, repo, evaluator, protected)
            base.update(run_data)
            base.update({
                "status": "passed" if passed else "failed",
                "completion": passed if live else None,
                "harness_pass": passed if not live else None,
                "scoring": evidence,
            })
            if not passed:
                if not evidence["protected_paths_unchanged"]:
                    base["error"] = "protected_paths_modified"
                elif evidence["public"]["exit_code"] != 0:
                    base["error"] = "public_test_command_failed"
                else:
                    base["error"] = "acceptance_script_failed"
    except Exception as exc:
        base.update({
            "mode": "live" if live else "offline_repo_oracle",
            "profile": profile if live else "harness-oracle",
            "status": "error",
            "completion": False if live else None,
            "harness_pass": False if not live else None,
            "error": f"{type(exc).__name__}: {exc}",
            "human_intervention_count": 0,
        })
    base["total_latency_seconds"] = round(time.monotonic() - started, 6)
    return base


def _summary(results: list[dict], *, live: bool, profile: str, manifest: dict, run_name: str) -> str:
    passed = sum(result["status"] == "passed" for result in results)
    errors = sum(result["status"] == "error" for result in results)
    human = sum(result.get("human_intervention_count", 0) for result in results)
    profile_names = {
        "baseline": "基础工具循环（baseline）", "retrieval": "增加代码定位（retrieval）",
        "repair": "增加失败反馈修复（repair）",
    }
    mode_names = {
        "live": "真实模型（live）", "offline_oracle": "离线预置补丁回放（offline_oracle）",
        "offline_repo_oracle": "真实仓库离线补丁回放（offline_repo_oracle）",
    }
    lines = [
        f"# 评测报告：{run_name}", "",
        f"- 任务清单哈希：`{manifest['manifest_hash']}`（v{manifest['version']}）",
        f"- 任务数：{len(results)}（通过 {passed}，评测或运行异常 {errors}）",
        f"- 运行方式：{mode_names.get(results[0]['mode'], results[0]['mode'])}",
        f"- 策略配置：{profile_names.get(profile, profile) if live else '预置补丁回放（harness-oracle）'}",
        f"- 人工介入次数：{human}",
    ]
    if live:
        completions = sum(result.get("completion") is True for result in results)
        latencies = [result["agent_latency_seconds"] for result in results if result.get("agent_latency_seconds") is not None]
        tool_calls = [result["tool_calls"] for result in results if result.get("tool_calls") is not None]
        token_rows = [result["token_usage"] for result in results if result.get("token_usage")]
        first = results[0]
        budget_names = {
            "max_repair_attempts": "最多修复轮数", "max_rounds": "模型轮数上限",
            "max_seconds": "最长耗时（秒）", "max_tokens": "Token 上限",
            "max_tool_calls": "工具调用次数上限",
        }
        budget = "；".join(f"{budget_names.get(key, key)}：{value}"
                          for key, value in sorted(first.get("budget", {}).items()))
        lines.extend([
            f"- 模型：`{first.get('model') or '未记录'}`",
            f"- 服务地址：`{first.get('endpoint') or 'Anthropic SDK 默认地址'}`",
            f"- 运行预算：{budget or '未记录'}",
            f"- Agent 任务完成率（按补丁验收计）：{completions}/{len(results)}（{completions / len(results):.1%}）",
            f"- Agent 平均耗时：{sum(latencies) / len(latencies):.3f} 秒" if latencies else "- Agent 平均耗时：未记录",
            (f"- 工具调用次数：{sum(tool_calls)}" if len(tool_calls) == len(results)
             else f"- 工具调用次数：记录不完整（{len(tool_calls)}/{len(results)} 个任务有记录）"),
            (f"- 实际 Token 用量：{sum(row['total_tokens'] for row in token_rows)}"
             if len(token_rows) == len(results)
             else f"- 实际 Token 用量：记录不完整（{len(token_rows)}/{len(results)} 个任务有记录）"),
        ])
    else:
        lines.extend([
            f"- 评测链路验收通过率：{passed}/{len(results)}（{passed / len(results):.1%}）",
            "- Agent 任务完成率：待测（未调用模型）",
            "- Agent 耗时、工具调用和实际 Token 用量：待测（未调用模型）",
            "",
            "> 此确定性回放只验证仓库初始化与外部评分链路，不代表 Agent 的任务完成能力。",
        ])
    status_names = {"passed": "通过", "failed": "未通过", "error": "执行异常"}
    split_names = {"dev": "开发集", "heldout": "留出集", "repo": "真实仓库"}
    error_names = {
        "public_tests_modified": "公开测试被修改", "frozen_public_tests_failed": "冻结的公开测试未通过",
        "acceptance_tests_failed": "外部验收测试未通过", "protected_paths_modified": "受保护文件被修改",
        "public_test_command_failed": "公开测试命令未通过", "acceptance_script_failed": "外部验收脚本未通过",
    }
    lines.extend(["", "| 任务 | 数据划分 | 结果 | 失败原因 |", "|---|---|---|---|"])
    for result in results:
        error = result.get("error") or ""
        reason = f"{error_names[error]}（`{error}`）" if error in error_names else error
        lines.append(f"| {result['task_id']} | {split_names.get(result['split'], result['split'])} | {status_names.get(result['status'], result['status'])} | {reason} |")
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="explicitly allow model API calls")
    parser.add_argument("--profile", choices=PROFILE_NAMES, default="baseline")
    parser.add_argument("--split", choices=("dev", "heldout", "all"), default="dev")
    parser.add_argument("--task", action="append", default=[], help="task id; repeat to select several")
    parser.add_argument("--repo-manifest", type=Path, help="local Git repository task declaration")
    parser.add_argument("--output", type=Path, default=EVAL_DIR / "results" / "latest")
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--max-rounds", type=int, default=20)
    parser.add_argument("--max-tool-calls", type=int, default=50)
    parser.add_argument("--max-seconds", type=float, default=300)
    parser.add_argument("--max-tokens", type=int, default=50000)
    parser.add_argument("--max-repair-attempts", type=int, default=2)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_mode = args.repo_manifest is not None
    manifest = load_repo_manifest(args.repo_manifest) if repo_mode else load_manifest()
    tasks = manifest["tasks"]
    if not repo_mode and args.split != "all":
        tasks = [task for task in tasks if task["split"] == args.split]
    if args.task:
        requested = set(args.task)
        tasks = [task for task in tasks if task["id"] in requested]
        missing = requested - {task["id"] for task in tasks}
        if missing:
            raise SystemExit(f"selected tasks are absent from split: {', '.join(sorted(missing))}")
    config = _model_config() if args.live else None
    if args.live:
        _scrub_model_credentials()
    limits = {
        "max_rounds": args.max_rounds,
        "max_tool_calls": args.max_tool_calls,
        "max_seconds": args.max_seconds,
        "max_tokens": args.max_tokens,
        "max_repair_attempts": args.max_repair_attempts,
    }
    run_name = args.run_name or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    task_runner = run_repo_task if repo_mode else run_task
    results = [task_runner(task, live=args.live, profile=args.profile, limits=limits, config=config) for task in tasks]
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.jsonl").write_text(
        "".join(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n" for result in results)
    )
    (args.output / "summary.md").write_text(
        _summary(results, live=args.live, profile=args.profile, manifest=manifest, run_name=run_name)
    )
    print(f"wrote {len(results)} results to {args.output}")
    return 0 if all(result["status"] == "passed" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
