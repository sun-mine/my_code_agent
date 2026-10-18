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
        # ====== 模型服务配置（默认使用本地 Ollama）======
        # Ollama 原生兼容 Anthropic Messages API，直接复用 Anthropic SDK
        # 切换其他服务时在 .env 覆盖这三个变量即可
        # 用 127.0.0.1 而非 localhost：避免 DNS 解析为 IPv6 ::1 导致连接失败
        self.base_url = os.getenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:11434")
        self.api_key = os.getenv("ANTHROPIC_AUTH_TOKEN") or "ollama"  # Ollama 不校验 key
        self.model = os.getenv("MODEL_ID", "qwen2.5:3b")

        self.token_threshold = int(os.getenv("TOKEN_THRESHOLD", "100000"))
        # 所有 agent 运行时数据统一放在 workdir/.agent/ 下
        _agent_dir = self.workdir / ".agent"
        self.team_dir = _agent_dir / "team"
        self.tasks_dir = _agent_dir / "tasks"
        self.skills_dir = self.workdir / "skills"   # skills 仍在项目根
        self.transcript_dir = _agent_dir / "transcripts"
        self.worktree_dir = _agent_dir / "worktrees"