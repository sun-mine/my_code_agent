"""Post-run diagnostics; never changes the frozen A/B implementation or its scores."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import re
import statistics
import tempfile
import time

from evals.search_ab import load, save, symbols, scheduled_runs, valid_pairs, digest
from evals.ollama_eval_client import post_json
from evals.hybrid_search import HybridIndex
from evals import runner
from tools.base import BASE_HANDLERS, set_workdir


def hits_for(task, definitions, spans):
    return [any(path == target and line <= end and last >= start for path, line, last in spans)
            for target, name in task["targets"] for start, end in [definitions[(target, name)]]]


def evidence(row, task, files, definitions):
    spans, per_call = [], []
    completed = {event["tool_use_id"]: event for event in row.get("events", [])
                 if event.get("type") == "tool_result"}
    for event in row.get("events", []):
        if event.get("type") != "tool_start":
            continue
        name, args = event["name"], event.get("input", {})
        result = completed.get(event["tool_use_id"], {})
        if not result or result.get("is_error"):
            continue
        current = []
        try:
            if name == "read_file":
                path = Path(args["path"]).as_posix()
                first = max(1, int(args.get("start_line", 1)))
                last = min(len(files[path].splitlines()), first + min(120, max(1, int(args.get("limit", 80)))) - 1)
                current = [(path, first, last)]
            elif name == "grep":
                output = str(BASE_HANDLERS[name](**args))[:8000]
                current = [(Path(path).as_posix(), int(line), int(line))
                           for path, line in re.findall(r"^(.+?):(\d+):", output, re.M)]
            elif name == "lookup_symbol":
                output = str(BASE_HANDLERS[name](**args))[:8000]
                current = [(Path(path).as_posix(), int(line), int(line))
                           for path, line in re.findall(r'"path":\s*"([^"]+)",\s*"line":\s*(\d+)', output)]
            elif name == "hybrid_search":
                search = next((s for s in row.get("hybrid_searches", [])
                               if s["query"] == args.get("query") and s.get("success")), None)
                if search:
                    current = [(hit["path"], hit["line"], hit["end_line"]) for hit in search["results"]]
        except (KeyError, TypeError, ValueError, OSError):
            current = []
        spans.extend(current)
        per_call.append({"tool": name, "input": args, "target_hits": hits_for(task, definitions, current)})
    found = hits_for(task, definitions, spans)
    return {"task_id": row["task_id"], "repeat": row["repeat"], "profile": row["profile"],
            "candidate_coverage": sum(found) / len(found), "candidate_hits": found,
            "final_recall": row["score"].get("recall_at_5", 0), "calls": per_call}


def render_report(output, summary):
    path = output / "comparison.md"
    original = path.read_text()
    original = re.sub(r"\n<!-- ANALYSIS_START -->.*?<!-- ANALYSIS_END -->\n", "\n", original, flags=re.S)
    groups = summary["groups"]
    a, b = groups["A"], groups["B"]
    lines = ["<!-- ANALYSIS_START -->", "", "**结论：本次没有取得足以判断 A/B 优劣的任务完成证据。**", "",
        f"生成模型为免费本地 `qwen2.5:3b`。正式计划 {summary['completed']}/32 次已记录，完整有效配对 {summary['valid_pairs']} 对，基础设施异常 {summary['infrastructure_errors']} 次。",
        f"源码定位 A {a['stages']['loc']['successes']}/12、B {b['stages']['loc']['successes']}/12；合成修复 A {a['stages']['repair']['successes']}/4、B {b['stages']['repair']['successes']}/4。", "",
        "有效配对表示执行与评分记录完整，并非任务完成。其中 4 次达到单轮模型输出上限，按有效失败保留，未从分母排除。", "",
        f"B 共 {b['stages']['loc']['runs'] + b['stages']['repair']['runs']} 次运行，实际调用混合检索的运行 {b['hybrid_used_runs']} 次，查询 {b['hybrid_attempts']} 次，成功查询 {b['hybrid_successes']} 次。",
        "工具可用但未调用时，只能判断这套配置下增加工具没有形成可观测收益，不能判断 BM25、Embedding 或 RRF 本身的优劣。",
        "模型反复出现未搜索即回答、把示例名字当成目标、未提交定位、未实际修改源码等执行问题。", "",
        "**项目选择：按代码少、逻辑清晰的要求，暂保留现有搜索；先让模型稳定调用工具并完成真实任务，再决定是否引入向量检索。当前没有证据支持宣称 B 提升了完成率或节省了有效任务的 Token。**", "",
        "### 分阶段实际成本", "", "下表是所有有效运行的平均成本；任务失败时，较少 Token 不代表优化收益。", "",
        "| 阶段 | 组别 | 平均 Agent 秒 | 平均生成模型 Token | 工具调用合计 |", "|---|---|---:|---:|---:|"]
    for kind, label in (("loc", "源码定位"), ("repair", "合成修复")):
        for profile, data in groups.items():
            stage = data["stages"][kind]
            lines.append(f"| {label} | {profile} | {stage['mean_seconds']:.2f} | {stage['mean_llm_tokens']:.1f} | {stage['tool_calls']} |")
    lines += ["", "### 工具返回的目标证据", "",
        "将成功执行的源码读取、关键词搜索、AST 定位、混合检索结果与冻结函数范围核对。目录中出现文件名不算找到目标代码；此指标与最终提交分开。", "",
        f"平均目标证据覆盖率：A {a['mean_candidate_coverage']:.1%}，B {b['mean_candidate_coverage']:.1%}。详见 mechanism-analysis.json。", ""]
    diagnostics_path = output / "retriever-diagnostics.json"
    if diagnostics_path.exists():
        diagnostic = load(diagnostics_path)
        lines += ["### 事后补充：直接调用检索器", "",
            "为区分检索器是否能召回与模型是否会调用工具，使用 6 道原始问题直接检索同一源码索引，比较 BM25、纯向量和 RRF 融合。这里没有 Agent 决策循环，不属于上面的 A/B 成功率，也不能证明修复能力提升。", "",
            "本次 BM25 使用简单中文单字与代码标识符分词，直接输入原始中文问题，未使用专门的中文词语分词器或查询改写；该结果不能代表优化后的 BM25，也不能等同于 A 的自主关键词搜索。", "",
            "指标为前 5 个代码块对标注函数范围的覆盖率，不是精确到缺陷行的定位准确率。", "",
            "| 问题 | BM25 | 向量 | RRF 融合 |", "|---|---:|---:|---:|"]
        for task in diagnostic["tasks"]:
            scores = task["rankers"]
            lines.append(f"| {task['task_id']} | {scores['bm25']['recall_at_5']:.1%} | {scores['dense']['recall_at_5']:.1%} | {scores['rrf']['recall_at_5']:.1%} |")
        means = {name: statistics.mean(task["rankers"][name]["recall_at_5"] for task in diagnostic["tasks"])
                 for name in ("bm25", "dense", "rrf")}
        lines += [f"| 平均 | {means['bm25']:.1%} | {means['dense']:.1%} | {means['rrf']:.1%} |", "",
            f"补充诊断耗时 {diagnostic['seconds']:.2f} 秒，查询向量 Token {diagnostic['embedding_tokens']}，单独记录，不混入正式 A/B 成本。", ""]
    lines += ["验证：标准后端 tests/ 共 176 项通过；本轮 76 个业务源文件内容哈希未变化。仅新增评测、测试和证据文件。",
        "两组关闭额外动作补偿与控制器自动修复；B 未自动预注入检索结果。先导运行单独保留并排除，任务为公开自建探索性样本，不能声称外部或保密 heldout 基准。", "", "<!-- ANALYSIS_END -->", ""]
    first, rest = original.split("\n", 1)
    path.write_text((first + "\n" + "\n".join(lines) + rest).replace("| False |", "| 未通过 |"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--diagnostics", action="store_true")
    args = parser.parse_args()
    output = args.output
    protocol, tasks, corpus = (load(output / name) for name in
                              ("protocol.json", "tasks.json", "source-snapshot.json"))
    rows = [load(path) for path in sorted((output / "runs").glob("*-r[12]-[AB].json"))]
    pairs = valid_pairs(rows, scheduled_runs(tasks), digest(protocol))
    if not pairs["complete"] or pairs["infra"]:
        raise ValueError("This post-run summary requires the complete experiment without infrastructure-invalid pairs")
    files, definitions = corpus["project"], symbols(corpus["project"])
    indexed_tasks = {t["id"]: t for t in tasks["localization"]}
    with tempfile.TemporaryDirectory(prefix="code-agent-evidence-replay-") as temporary:
        root = Path(temporary)
        runner._write_files(root, files)
        set_workdir(root)
        coverage = [evidence(row, indexed_tasks[row["task_id"]], files, definitions)
                    for row in pairs["rows"] if row["kind"] == "loc"]
    summary = {"protocol_hash": digest(protocol), "completed": len(rows), "valid_pairs": len(pairs["rows"]) // 2,
               "missing": pairs["missing"], "infrastructure_errors": pairs["infra"], "groups": {}}
    for profile in ("A", "B"):
        group = [r for r in pairs["rows"] if r["profile"] == profile]
        stages = {}
        for kind in ("loc", "repair"):
            selected = [r for r in group if r["kind"] == kind]
            tokens = [r["llm_input_tokens"] + r["llm_output_tokens"] for r in selected if r.get("usage_complete")]
            durations = [r["agent_seconds"] for r in selected if r.get("agent_seconds") is not None]
            stages[kind] = {"runs": len(selected),
                "successes": sum(r["score"].get("all_targets_found" if kind == "loc" else "patch_accepted", False) for r in selected),
                "mean_recall": statistics.mean(r["score"].get("recall_at_5", 0) for r in selected) if selected and kind == "loc" else None,
                "mean_seconds": statistics.mean(durations) if durations else None,
                "mean_llm_tokens": statistics.mean(tokens) if tokens else None,
                "usage_complete_runs": len(tokens),
                "tool_calls": sum(r.get("outcome", {}).get("tool_calls", 0) for r in selected),
                "stop_reasons": dict(Counter(r.get("outcome", {}).get("stop_reason") for r in selected))}
        searches = [s for r in group for s in r.get("hybrid_searches", [])]
        seen = [c["candidate_coverage"] for c in coverage if c["profile"] == profile]
        summary["groups"][profile] = {"stages": stages,
            "hybrid_used_runs": sum(bool(r.get("hybrid_searches")) for r in group),
            "hybrid_attempts": len(searches), "hybrid_successes": sum(s.get("success", False) for s in searches),
            "query_embedding_tokens": sum(s["embedding_tokens"] for s in searches) if all(s.get("embedding_tokens") is not None for s in searches) else None,
            "mean_candidate_coverage": statistics.mean(seen) if seen else None}
    save(output / "mechanism-analysis.json", {"summary": summary, "localization_evidence": coverage,
         "coverage_note": "Successful tool outputs only; directory listings excluded. Source spans are replayed from the frozen snapshot. This is candidate evidence coverage, not final task success or a new A/B score."})
    if args.diagnostics:
        if not pairs["complete"]:
            raise ValueError("Do not run extra model diagnostics while the main evaluation is incomplete")
        started = time.monotonic()
        response = post_json(protocol["endpoint"], "/api/embed", {
            "model": protocol["embedding_model"], "input": [t["prompt"] for t in tasks["localization"]],
            "truncate": False, "keep_alive": "5m"}, timeout=120)
        with gzip.open(output / "index.json.gz", "rt") as stream:
            data = json.load(stream)["project"]
        index = HybridIndex(data["chunks"], data["vectors"])
        diagnostics = []
        for task, vector in zip(tasks["localization"], response["embeddings"]):
            candidates = index.search(task["prompt"], vector, limit=len(data["chunks"]))
            rankers = {"bm25": sorted((c for c in candidates if c["bm25_rank"] is not None), key=lambda c:c["bm25_rank"])[:5],
                       "dense": sorted(candidates, key=lambda c:c["dense_rank"])[:5], "rrf": candidates[:5]}
            scores = {}
            for name, results in rankers.items():
                hits = hits_for(task, definitions, [(c["path"],c["line"],c["end_line"]) for c in results])
                scores[name] = {"recall_at_5": sum(hits)/len(hits), "target_hits": hits,
                               "results": [dict(c,text=c["text"][:700]) for c in results]}
            diagnostics.append({"task_id":task["id"],"query":task["prompt"],"rankers":scores})
        save(output / "retriever-diagnostics.json", {"scope":"Post-hoc retriever diagnostic using the original full task prompts; NOT Agentic A/B scores and not evidence of completed repairs",
             "protocol_hash":digest(protocol),"seconds":time.monotonic()-started,
             "embedding_tokens":response.get("prompt_eval_count"),"tasks":diagnostics})
        post_json(protocol["endpoint"], "/api/embed", {"model":protocol["embedding_model"],"input":[],"keep_alive":0},timeout=60)
    render_report(output, summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
