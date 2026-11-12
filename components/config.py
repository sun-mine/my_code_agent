"""统一读取根目录 .env；日常配置只需编辑 .env。"""
import os
from pathlib import Path

from dotenv import load_dotenv
from core.run_policy import RunLimits

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROVIDERS = {
    "ollama": ("http://127.0.0.1:11434", "qwen2.5:3b", "api_key"),
    "deepseek": ("https://api.deepseek.com/anthropic", "deepseek-flash", "bearer"),
    "kimi": ("https://api.moonshot.cn/anthropic", "kimi-k3", "bearer"),
    "anthropic": ("https://api.anthropic.com", "", "api_key"),
    "custom": ("", "", "api_key"),
}


def load_environment():
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def env(name, default=""):
    return os.getenv(name, str(default)).strip()


class Config:
    def __init__(self, workdir=None, *, provider=None, model_override=None):
        # 模型：只读取当前供应商的配置，不借用其他供应商的密钥。
        self.provider = (provider or env("LLM_PROVIDER") or "ollama").lower()
        if self.provider not in PROVIDERS:
            raise ValueError("Unknown LLM_PROVIDER; choose " + ", ".join(PROVIDERS))
        prefix = self.provider.upper()
        url, model, auth = PROVIDERS[self.provider]
        self.base_url = env(f"{prefix}_BASE_URL", url).rstrip("/")
        self.model = (model_override or "").strip() or env(f"{prefix}_MODEL", model)
        self.api_key = env(f"{prefix}_API_KEY", "ollama" if self.provider == "ollama" else "")
        self.auth_mode = env(f"{prefix}_AUTH_MODE", auth)
        extra_models = env(f"{prefix}_MODELS").split(",")
        self.available_models = tuple(dict.fromkeys(
            m.strip() for m in [self.model, *extra_models] if m.strip()
        ))

        # Web 服务与交互。
        self.host = env("API_HOST", "0.0.0.0") or "0.0.0.0"
        self.port = int(env("API_PORT", 8000))
        self.frontend_port = int(env("FRONTEND_PORT", 5173))
        self.proxy_target = (env("API_PROXY_TARGET") or f"http://127.0.0.1:{self.port}").rstrip("/")
        origins = env("API_CORS_ORIGINS", "*")
        self.cors_origins = "*" if origins == "*" else [x.strip() for x in origins.split(",") if x.strip()]
        self.approval_timeout = float(env("TOOL_APPROVAL_TIMEOUT", 120))
        self.mode = env("AGENT_MODE", "auto")

        # 运行预算与可选集成。
        self.token_threshold = int(env("TOKEN_THRESHOLD", 100000))
        self.verification_command = env("AGENT_VERIFY_COMMAND") or None
        self.run_limits = RunLimits(
            max_rounds=int(env("AGENT_MAX_ROUNDS", 40)),
            max_tool_calls=int(env("AGENT_MAX_TOOL_CALLS", 100)),
            max_seconds=float(env("AGENT_MAX_SECONDS", 600)),
            max_tokens=int(env("AGENT_MAX_TOKENS", 100000)),
            max_repair_attempts=int(env("AGENT_MAX_REPAIR_ATTEMPTS", 2)),
        )
        self.github_mcp_token = env("GITHUB_MCP_TOKEN")
        self.github_mcp_timeout = float(env("GITHUB_MCP_TIMEOUT", 20))
        self.github_mcp_status = {"status": "disabled", "tools": []}
        self.a2a_review_url = env("A2A_REVIEW_URL")
        self.a2a_review_token = env("A2A_REVIEW_TOKEN")
        self.a2a_review_timeout = float(env("A2A_REVIEW_TIMEOUT", 120))

        # 路径由工作目录派生，无需逐项配置。
        self.workdir = Path(workdir or env("AGENT_WORKDIR") or Path.cwd()).expanduser().resolve()
        agent_dir = self.workdir / ".agent"
        self.team_dir = agent_dir / "team"
        self.tasks_dir = agent_dir / "tasks"
        self.transcript_dir = agent_dir / "transcripts"
        self.worktree_dir = agent_dir / "worktrees"
        self.skills_dir = self.workdir / "skills"

    def client_kwargs(self):
        # 创建客户端时才检查模型，单独读取服务配置不要求填写密钥。
        for suffix, value in (("BASE_URL", self.base_url), ("MODEL", self.model), ("API_KEY", self.api_key)):
            if not value:
                raise ValueError(f"Missing {self.provider.upper()}_{suffix}")
        if self.auth_mode not in {"api_key", "bearer"}:
            raise ValueError("AUTH_MODE must be api_key or bearer")
        kwargs = {"base_url": self.base_url, "api_key": self.api_key}
        if self.auth_mode == "bearer":
            kwargs["default_headers"] = {"Authorization": f"Bearer {self.api_key}"}
        return kwargs
