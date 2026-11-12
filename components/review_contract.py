"""Bounded, versioned data exchanged with the independent review agent."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from components.run_report import _is_sensitive_path

IDENTITY_FIELDS = ("session_id", "run_id", "invocation_id", "patch_hash", "snapshot_hash")
_EVIDENCE_FIELDS = ("requirement", "diff", "snippets", "test_results", "context_limits")
_MAX_PACKAGE_BYTES = 128_000


def normalize_json(value):
    """A2A DataPart uses protobuf Value, whose JSON numbers arrive as doubles."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Review numbers must be finite")
        return int(value) if value.is_integer() else value
    if isinstance(value, dict):
        return {key: normalize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_json(item) for item in value]
    return value


def digest(value) -> str:
    data = value if isinstance(value, str) else json.dumps(normalize_json(value), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Snippet(_Model):
    path: str = Field(min_length=1, max_length=500)
    start_line: int = Field(ge=1)
    content: str = Field(max_length=8_000)


class ReviewPackage(_Model):
    schema_version: Literal[1] = 1
    session_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    invocation_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    patch_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    requirement: str = Field(max_length=6_000)
    diff: str = Field(max_length=48_000)
    snippets: list[Snippet] = Field(max_length=6)
    test_results: list[dict] = Field(max_length=6)
    context_limits: list[str] = Field(max_length=20)


class Finding(_Model):
    severity: Literal["high", "medium", "low"]
    file: str = Field(min_length=1, max_length=500)
    line: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2_000)
    evidence: str = Field(min_length=1, max_length=2_000)
    suggestion: str = Field(min_length=1, max_length=2_000)


class ReviewArtifact(_Model):
    schema_version: Literal[1] = 1
    session_id: str
    run_id: str
    invocation_id: str
    patch_hash: str
    snapshot_hash: str
    verdict: Literal["no_findings", "changes_requested"]
    findings: list[Finding] = Field(max_length=20)
    context_limits: list[str] = Field(max_length=30)
    usage: dict[str, int | None]


def seal_package(data: dict) -> dict:
    package = {"schema_version": 1, "snippets": [], "test_results": [], "context_limits": [], **data}
    package["patch_hash"] = digest(package["diff"])
    package["snapshot_hash"] = digest({key: package[key] for key in _EVIDENCE_FIELDS})
    package["invocation_id"] = digest([package["session_id"], package["run_id"], package["snapshot_hash"]])
    return validate_package(package)


def validate_package(package: dict) -> dict:
    package = ReviewPackage.model_validate(normalize_json(package)).model_dump()
    if len(json.dumps(package, ensure_ascii=False).encode("utf-8")) > _MAX_PACKAGE_BYTES:
        raise ValueError("Review package exceeds 128 KB; reduce the patch or context")
    if package["patch_hash"] != digest(package["diff"]):
        raise ValueError("Review patch hash mismatch")
    if package["snapshot_hash"] != digest({key: package[key] for key in _EVIDENCE_FIELDS}):
        raise ValueError("Review snapshot hash mismatch")
    if package["invocation_id"] != digest([package["session_id"], package["run_id"], package["snapshot_hash"]]):
        raise ValueError("Review invocation identity mismatch")
    for snippet in package["snippets"]:
        path = PurePosixPath(snippet["path"])
        if path.is_absolute() or ".." in path.parts or _is_sensitive_path(str(path)):
            raise ValueError("Review snippets must use non-sensitive relative paths")
    return package


def validate_review(artifact: dict, package: dict) -> dict:
    artifact = ReviewArtifact.model_validate(normalize_json(artifact)).model_dump()
    if any(artifact[key] != package[key] for key in IDENTITY_FIELDS):
        raise ValueError("Review artifact does not belong to this run and snapshot")
    if (artifact["verdict"] == "changes_requested") != bool(artifact["findings"]):
        raise ValueError("Review verdict and findings disagree")
    for key in ("input_tokens", "output_tokens"):
        value = artifact["usage"].get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError("Review usage must be non-negative or unknown")
    for finding in artifact["findings"]:
        path = PurePosixPath(finding["file"])
        if path.is_absolute() or ".." in path.parts or _is_sensitive_path(str(path)):
            raise ValueError("Invalid review finding path")
        available = any(
            snippet["path"] == finding["file"]
            and snippet["start_line"] <= finding["line"]
            < snippet["start_line"] + len(snippet["content"].splitlines())
            for snippet in package["snippets"]
        )
        if not available:
            current = None
            old_path = None
            for line in package["diff"].splitlines():
                if line.startswith("--- a/"):
                    old_path = line[6:]
                elif line.startswith("+++ b/"):
                    current = line[6:]
                elif line == "+++ /dev/null":
                    current = old_path
                elif line.startswith("@@") and current == finding["file"]:
                    hunk = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
                    if hunk:
                        start, count = int(hunk[3]), int(hunk[4] or 1)
                        if count == 0:
                            start, count = int(hunk[1]), int(hunk[2] or 1)
                        available |= start <= finding["line"] < start + count
        if not available:
            raise ValueError("Finding location is outside the supplied source and diff")
    if len(json.dumps(artifact, ensure_ascii=False).encode("utf-8")) > 128_000:
        raise ValueError("Review artifact exceeds 128 KB")
    return artifact


def build_package(report: dict, history: list, workdir: str) -> dict:
    """Collect bounded source snippets without exposing shared host paths."""
    limits = ["仅审查收到的 diff、代码片段和执行日志，未访问完整仓库。"]
    requirement = ""
    for message in reversed(history):
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        text = content if isinstance(content, str) else "\n".join(
            block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
        )
        if text and not text.startswith("<plan-execute>"):
            requirement = text[:6_000]
            if len(text) > 6_000:
                limits.append("需求文本截取前 6000 字符。")
            break
    root = Path(workdir).resolve()
    snippets = []
    for file in report.get("files", [])[:6]:
        relative = file["path"]
        target = root / relative
        if _is_sensitive_path(relative) or target.is_symlink():
            continue
        try:
            target.resolve().relative_to(root)
            if target.stat().st_size > 256_000:
                limits.append(f"{relative} 较大，未提供全文。")
                continue
            lines = target.read_text(encoding="utf-8").splitlines()
            content = "\n".join(lines[:160])[:8_000]
            if len(lines) > 160 or len("\n".join(lines[:160])) > 8_000:
                limits.append(f"{relative} 仅提供文件前部片段，其余变更以 diff 为准。")
            snippets.append({"path": relative, "start_line": 1, "content": content})
        except (OSError, UnicodeError, ValueError):
            limits.append(f"{relative} 无可读的当前源码片段。")
    commands = []
    for command in report.get("commands", [])[-6:]:
        item = {key: command.get(key) for key in ("command", "exit_code", "timed_out", "cancelled")}
        item["stdout"] = command.get("stdout", "")[-1_500:]
        item["stderr"] = command.get("stderr", "")[-1_500:]
        commands.append(item)
    if commands:
        limits.append("执行记录最多包含最近六条命令，每条 stdout/stderr 仅保留末尾 1500 字符。")
    if len(report.get("files", [])) > 6:
        limits.append("仅提供最多六个文件的源码片段。")
    if report.get("warnings"):
        limits.extend(str(warning)[:500] for warning in report["warnings"][:6])
    return seal_package({
        "session_id": report["session_id"], "run_id": report["run_id"],
        "requirement": requirement, "diff": report.get("diff", ""),
        "snippets": snippets, "test_results": commands, "context_limits": limits,
    })
