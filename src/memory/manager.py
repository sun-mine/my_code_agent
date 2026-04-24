import os
import json
from pathlib import Path
from typing import Dict, Any, List

class MemoryManager:
    """
    持久化记忆管理机制：全局记忆 (~/.claude/ 或类似)、会话记忆、存储库记忆。
    """
    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root)
        self.global_memory_dir = Path.home() / ".code_agent" / "memories"
        self.session_memory_dir = self.workspace_root / ".agent_memories" / "session"
        self.repo_memory_dir = self.workspace_root / ".agent_memories" / "repo"
        
        # 初始化目录
        for d in [self.global_memory_dir, self.session_memory_dir, self.repo_memory_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def write_memory(self, scope: str, name: str, content: str):
        """写入记忆"""
        target_dir = self._get_dir(scope)
        file_path = target_dir / f"{name}.md"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            
    def read_memory(self, scope: str, name: str) -> str:
        """读取指定记忆片段"""
        target_dir = self._get_dir(scope)
        file_path = target_dir / f"{name}.md"
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def get_all_memories(self, scope: str = "global") -> Dict[str, str]:
        """获取全量记忆(作为上下文注入)"""
        target_dir = self._get_dir(scope)
        memories = {}
        for file in target_dir.glob("*.md"):
            with open(file, "r", encoding="utf-8") as f:
                memories[file.stem] = f.read()
        return memories

    def _get_dir(self, scope: str) -> Path:
        if scope == "session": return self.session_memory_dir
        if scope == "repo": return self.repo_memory_dir
        return self.global_memory_dir

class TranscriptLogger:
    """
    会话转录本 (JSONL)，提供极简和故障恢复支持。
    """
    def __init__(self, workspace_root: str):
        self.log_file = Path(workspace_root) / ".agent_memories" / "transcript.jsonl"
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        
    def log_message(self, role: str, content: str, metadata: dict = None):
        entry = {
            "role": role,
            "content": content,
            "metadata": metadata or {}
        }
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

