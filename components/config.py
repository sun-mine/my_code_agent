import os
from pathlib import Path

class Config:
    def __init__(self, workdir: str = None):
        if workdir:
            self.workdir = Path(workdir).resolve()
        elif os.getenv("AGENT_WORKDIR"):
            self.workdir = Path(os.getenv("AGENT_WORKDIR")).resolve()
        else:
            self.workdir = Path.cwd()
        self.token_threshold = int(os.getenv("TOKEN_THRESHOLD", "100000"))
        # 所有 agent 运行时数据统一放在 workdir/.agent/ 下
        _agent_dir = self.workdir / ".agent"
        self.team_dir = _agent_dir / "team"
        self.tasks_dir = _agent_dir / "tasks"
        self.skills_dir = self.workdir / "skills"   # skills 仍在项目根
        self.transcript_dir = _agent_dir / "transcripts"
        self.worktree_dir = _agent_dir / "worktrees"