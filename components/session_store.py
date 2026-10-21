"""Persistent, thread-safe storage for chat sessions."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Union


def to_jsonable(value: Any) -> Any:
    """Convert Anthropic/Pydantic content blocks without discarding their fields."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return to_jsonable(model_dump(mode="json", exclude_none=False))
        except TypeError:
            return to_jsonable(model_dump())

    as_dict = getattr(value, "dict", None)
    if callable(as_dict):
        return to_jsonable(as_dict())

    fields = {}
    for name in (
        "type", "text", "thinking", "signature", "id", "name", "input",
        "tool_use_id", "content", "is_error", "cache_control", "source",
    ):
        if hasattr(value, name):
            fields[name] = to_jsonable(getattr(value, name))
    if fields:
        return fields
    return str(value)


class SessionStore:
    """Own session records and persist each record with an atomic replace."""

    def __init__(self, directory: Union[Path, str]):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._records: Dict[str, Dict[str, Any]] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        with self._lock:
            for path in self.directory.glob("session_*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    continue
                sid = data.get("id") or path.stem
                data["id"] = sid
                data.setdefault("history", [])
                data.setdefault("mode", "auto")
                data.setdefault("title", "")
                data.setdefault("event_seq", 0)
                self._records[sid] = data

    @staticmethod
    def new_id() -> str:
        return f"session_{uuid.uuid4()}"

    def _path(self, session_id: str) -> Path:
        if not session_id.startswith("session_") or Path(session_id).name != session_id:
            raise ValueError("invalid session id")
        return self.directory / f"{session_id}.json"

    def _atomic_write(self, record: Dict[str, Any]) -> None:
        path = self._path(record["id"])
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(to_jsonable(record), ensure_ascii=False, indent=2)
        try:
            with temp.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def create(
        self,
        workdir: str,
        mode: str = "auto",
        title: str = "",
        session_id: Optional[str] = None,
        history: Optional[Iterable[Dict[str, Any]]] = None,
        created_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        record = {
            "id": session_id or self.new_id(),
            "title": title[:80],
            "created_at": created_at or now,
            "updated_at": now,
            "message_count": 0,
            "workdir": str(Path(workdir).expanduser().resolve()),
            "history": list(history or []),
            "mode": mode if mode in {"auto", "plan", "manual"} else "auto",
            "event_seq": 0,
        }
        record["message_count"] = len(record["history"])
        with self._lock:
            if record["id"] in self._records:
                raise ValueError("session already exists")
            self._atomic_write(record)
            self._records[record["id"]] = record
            return deepcopy(to_jsonable(record))

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self._records.get(session_id)
            return deepcopy(record) if record is not None else None

    def save(self, record: Dict[str, Any]) -> Dict[str, Any]:
        saved = dict(record)
        saved["history"] = to_jsonable(saved.get("history", []))
        saved["message_count"] = len(saved["history"])
        saved["updated_at"] = time.time()
        with self._lock:
            self._atomic_write(saved)
            self._records[saved["id"]] = saved
            return deepcopy(saved)

    def retain(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Keep a recoverable in-memory copy when durable persistence fails."""
        retained = dict(record)
        retained["history"] = to_jsonable(retained.get("history", []))
        retained["message_count"] = len(retained["history"])
        retained["updated_at"] = time.time()
        with self._lock:
            self._records[retained["id"]] = retained
            return deepcopy(retained)

    def update(self, session_id: str, **changes: Any) -> Dict[str, Any]:
        with self._lock:
            current = self._records.get(session_id)
            if current is None:
                raise KeyError(session_id)
            updated = dict(current)
            updated.update(changes)
            return self.save(updated)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            if session_id not in self._records:
                return False
            path = self._path(session_id)
            path.unlink(missing_ok=True)
            del self._records[session_id]
            return True

    def list(self, workdir: Optional[str] = None) -> list[Dict[str, Any]]:
        selected = None
        if workdir:
            selected = str(Path(workdir).expanduser().resolve())
        with self._lock:
            records = [
                deepcopy(record)
                for record in self._records.values()
                if selected is None or record.get("workdir") == selected
            ]
        records.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
        return records
