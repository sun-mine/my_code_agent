"""模型选择与切换路由。"""
from flask import Blueprint, request
from anthropic import Anthropic

from components.config import Config

import api_server
from api.agent_core import _require_agent, _detect_available_models
from api.session_helpers import _ok, _err

models_bp = Blueprint("models", __name__)


@models_bp.route("/api/models")
def list_models():
    models = _detect_available_models()
    cfg = api_server._cfg or Config()
    return _ok({"models": models, "current": cfg.model, "current_provider": cfg.provider})


@models_bp.route("/api/model")
def get_model():
    if not api_server.agent_config:
        return _ok({"model": api_server._cfg.model if api_server._cfg else ""})
    return _ok({"model": api_server.agent_config.get("model", "")})


@models_bp.route("/api/model", methods=["POST"])
def set_model():
    r = _require_agent()
    if r:
        return r
    data = request.get_json(force=True)
    new_model = (data.get("model") or "").strip()
    if not new_model:
        return _err("model required")
    with api_server._lock:
        if api_server._running_ctx is not None:
            return _err("agent is running", 409)
        cfg = api_server._cfg
        if cfg is None:
            return _err("model configuration unavailable", 503)
        provider = (data.get("provider") or cfg.provider).strip().lower()
        choices = _detect_available_models()
        choice = next((m for m in choices if m["provider"] == provider and m["id"] == new_model), None)
        if choice is None:
            return _err("model is not in the configured models list")
        if not choice["configured"]:
            return _err("请先在 .env 中填写该服务商的地址、密钥和模型")
        team = api_server.team_manager
        if team and any(thread.is_alive() for thread in team.threads.values()):
            return _err("团队任务尚未结束，请稍后切换模型", 409)
        try:
            target = Config(workdir=cfg.workdir, provider=provider, model_override=new_model)
            client = Anthropic(**target.client_kwargs())
        except Exception:
            return _err("模型客户端创建失败，请检查该服务商的配置", 400)
        old_client = api_server.agent_instance.client
        for name in ("provider", "model", "base_url", "api_key", "auth_mode", "available_models"):
            setattr(cfg, name, getattr(target, name))
        api_server.agent_instance.client = client
        api_server.agent_instance.model = new_model
        api_server.agent_config.update(model=new_model, provider=provider)
        if team:
            team.client = client
            team.model = new_model
        try:
            old_client.close()
        except Exception:
            api_server.app.logger.warning("Could not close the previous model client")
    return _ok({"model": new_model, "provider": provider, "message": "model switched"})
