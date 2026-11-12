"""Review the finished patch through A2A and keep its original run ownership."""
import hashlib
import json
import os
from pathlib import Path
import uuid

from flask import Blueprint, request

import api_server
from api import callbacks
from api.session_helpers import _err, _ok, _resolve_session
from components.command_runner import CancellationToken
from components.review_contract import build_package, digest, validate_package, validate_review

reviews_bp = Blueprint("reviews", __name__)
_resuming = {}


def is_review_resuming() -> bool:
    """Called under the shared run admission lock."""
    return bool(_resuming)


def _write(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _fingerprints(root: str, files: list) -> dict:
    root = Path(root).resolve()
    values = {}
    for file in files:
        relative = file["path"]
        target = root / relative
        try:
            target.resolve().relative_to(root)
            if target.is_symlink():
                values[relative] = "symlink:" + digest(os.readlink(target))
            elif not target.exists():
                values[relative] = None
            elif target.stat().st_size > 8_000_000:
                raise ValueError("Review input file exceeds 8 MB")
            else:
                values[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            values[relative] = "unreadable"
    return values


def _client(config: dict):
    from components.a2a_review import A2AReviewClient
    return A2AReviewClient(config["url"], timeout=float(config["timeout"]), token=config["token"])


def run_review(context, plan_mode: bool):
    config = getattr(context, "review_config", {})
    terminal = context.pending_run_end or {}
    if plan_mode or not config.get("url") or terminal.get("outcome") != "completed":
        context.phase = "finishing"
        return
    context.phase = "review_pending"
    directory = context.run_reporter.directory
    try:
        report = context.run_reporter.finalize("completed")
        with api_server._lock:
            if context.review_token.is_cancelled:
                context.review_result = {"status": "cancelled", "error": "审查尚未提交，已取消。"}
                context.phase = "finishing"
                return
            if not report["diff"]:
                context.review_result = {"status": "skipped", "error": "没有可审查的 Git diff。"}
                context.phase = "finishing"
                return
        root = str(context.run_reporter.repo_root or context.workdir)
        package = build_package(report, context.baseline_history, root)
        context.review_package = package
        _write(directory / "review-package.json", package)
        _write(directory / "review-local.json", {
            "session_id": context.session_id, "run_id": context.run_id,
            "snapshot_hash": package["snapshot_hash"], "source_root": root,
            "delivery_outcome": report["outcome"],
            "file_hashes": _fingerprints(root, report["files"]),
        })
        context.phase = "reviewing"
        callbacks._push_run_event(context, {"type": "agent_state", "state": "reviewing"})

        def update(review):
            # This closure captures the original context, never the active chat.
            if context.phase == "reviewing":
                callbacks._push_run_event(context, {"type": "a2a_review", "review": review})

        context.review_result = _client(config).review(
            package, directory / "a2a-review.json",
            cancellation_token=context.review_token, on_update=update,
        )
    except Exception as exc:
        context.review_result = {"status": "failed", "error": f"A2A 审查未完成：{type(exc).__name__}"}
        api_server.app.logger.exception("A2A review failed for run %s", context.run_id)
    finally:
        context.phase = "finishing"


def attach_review(context, report: dict) -> dict:
    review = getattr(context, "review_result", None)
    if review is None:
        return report
    review = dict(review)
    if review.get("status") == "completed" and context.review_package:
        try:
            review["artifact"] = validate_review(review.get("artifact"), context.review_package)
        except ValueError:
            review.update(status="failed", error="审查产物与该运行或快照不匹配。")
        if digest(report["diff"]) != context.review_package["patch_hash"]:
            review.update(status="stale", error="审查期间补丁已变化，此结果已过期。")
    report = {**report, "review": review}
    artifact = review.get("artifact") or {}
    if review["status"] == "cancelled" and report["outcome"] == "completed":
        report["outcome"] = "interrupted"
    elif report["outcome"] == "completed" and (
        review["status"] not in {"completed", "skipped"}
        or artifact.get("verdict") == "changes_requested"
    ):
        report["outcome"] = "incomplete"
    context.review_result = review
    try:
        context.run_reporter.save(report)
    except Exception:
        report = context.run_reporter.revise(report, "A2A 审查报告持久化失败。")
    return report


@reviews_bp.route("/api/reviews/resume", methods=["POST"])
def resume_review():
    """Reconnect to the saved remote task without starting another code run."""
    data = request.get_json(silent=True) or {}
    sid, run_id = data.get("session_id"), data.get("run_id")
    if not isinstance(sid, str) or not sid or not isinstance(run_id, str) or not run_id:
        return _err("session_id and run_id required", 400)
    with api_server._lock:
        if api_server._running_ctx is not None:
            return _err("当前有运行中的任务，请等待其结束后恢复审查。", 409)
        record, store = _resolve_session(sid)
        reports = record.get("delivery_reports", []) if record else []
        report = next((item for item in reports if item.get("run_id") == run_id), None)
        if report is None or not isinstance(run_id, str) or Path(run_id).name != run_id:
            return _err("未找到属于该会话的运行报告。", 404)
        config = {
            "url": getattr(api_server._cfg, "a2a_review_url", ""),
            "timeout": getattr(api_server._cfg, "a2a_review_timeout", "120"),
            "token": getattr(api_server._cfg, "a2a_review_token", ""),
        }
        if not config["url"]:
            return _err("尚未配置 A2A_REVIEW_URL。", 409)
        directory = Path(record["workdir"]) / ".agent" / "runs" / run_id
        key = (sid, run_id)
        if key in _resuming:
            return _err("该审查正在恢复查询。", 409)
        token = CancellationToken()
        _resuming[key] = token
    try:
        package = validate_package(json.loads((directory / "review-package.json").read_text()))
        if package["session_id"] != sid or package["run_id"] != run_id:
            return _err("审查材料不属于该会话和运行。", 409)
        review = _client(config).review(package, directory / "a2a-review.json", cancellation_token=token)
        local = json.loads((directory / "review-local.json").read_text())
        files = [{"path": name} for name in local["file_hashes"]]
        if _fingerprints(local["source_root"], files) != local["file_hashes"]:
            review.update(status="stale", error="工作区文件已变化，恢复的审查结果已过期。")
        updated = {**report, "review": review}
        if local.get("delivery_outcome") == "completed":
            updated["outcome"] = (
                "interrupted" if review["status"] == "cancelled" else
                "completed" if review["status"] == "completed" and
                (review.get("artifact") or {}).get("verdict") == "no_findings" else "incomplete"
            )
        with api_server._lock:
            latest = store.get(sid)
            if latest is None:
                return _err("会话已不存在。", 409)
            latest["delivery_reports"] = [updated if item.get("run_id") == run_id else item for item in latest.get("delivery_reports", [])]
            _write(directory / "report.json", updated)
            store.save(latest)
        return _ok(updated)
    except (OSError, ValueError, ImportError):
        return _err("无法恢复审查：材料缺失、身份不符或服务不可用。", 409)
    finally:
        with api_server._lock:
            _resuming.pop(key, None)


@reviews_bp.route("/api/reviews/cancel", methods=["POST"])
def cancel_resumed_review():
    data = request.get_json(silent=True) or {}
    with api_server._lock:
        token = _resuming.get((data.get("session_id"), data.get("run_id")))
        if token is None:
            return _err("未找到属于该会话和运行的恢复查询。", 409)
        token.cancel()
    return _ok({"status": "cancelling", "interrupt_requested": True})
