"""模型选择与切换路由。"""
from flask import Blueprint, request

import api_server
from api.agent_core import _require_agent, _detect_available_models, _is_ollama
from api.session_helpers import _ok, _err

models_bp = Blueprint("models", __name__)


@models_bp.route("/api/models")
def list_models():
    models = _detect_available_models()
    current = api_server._cfg.model if api_server._cfg else (models[0]["id"] if models else "")
    return _ok({"models": models, "current": current})


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
    # Ollama 允许任意本地模型名；其他服务仍限制前缀
    if not _is_ollama():
        allowed = ("claude-", "deepseek-", "deepseek-v4-")
        if not any(new_model.startswith(p) for p in allowed):
            return _err(f"unsupported model: {new_model}")
    with api_server._lock:
        if api_server._running_ctx is not None:
            return _err("agent is running", 409)
        api_server.agent_instance.model = new_model
        api_server.agent_config["model"] = new_model
    return _ok({"model": new_model, "message": "model switched"})
