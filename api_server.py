"""
Web API 服务器 - ClaudeCode 风格前端的后端
提供 RESTful API 和 WebSocket 实时事件推送

核心逻辑按职责拆分到 api/ 包：
- session_helpers：响应封装、会话存储与快照
- callbacks：工具审批与事件推送
- agent_core：system prompt 构建、模型探测、agent 初始化
- run_lifecycle：一次 run 的启动/执行/收尾
- routes/*：按领域分组的 Flask Blueprint
- socket_handlers：Socket.IO 事件处理
"""
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO
from dotenv import load_dotenv

load_dotenv(override=True)

sys.path.insert(0, str(Path(__file__).parent))

from components.session_store import SessionStore  # noqa: E402

# ====== Flask & SocketIO ======
app = Flask(__name__)
CORS(app, origins="*")
# threading 模式：稳定可靠；断线重连与数据恢复由前端 useAgentSocket 自动处理
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ====== 全局状态 ======
_lock = threading.RLock()
_agent_init_lock = threading.RLock()
agent_instance = None
conversation_history = []
memory_manager = None
safety_manager = None
error_manager = None
todo_manager = None
task_manager = None
team_manager = None
bg_manager = None
agent_config = None
_run_thread = None
_agent_lock = threading.Lock()   # 防止并发 run
_is_running = False              # Agent 当前是否在运行
_cfg = None                      # 缓存 config，用于动态重建 system prompt
_skills = None                   # 缓存 skills，用于动态重建 system prompt
_session_store: Optional[SessionStore] = None
_session_stores: Dict[str, SessionStore] = {}
_active_session_id: Optional[str] = None
_active_store_key: Optional[str] = None


@dataclass
class RunContext:
    session_id: str
    run_id: str
    history: list
    baseline_history: list
    agent: Any
    mode: str
    workdir: str
    title: str
    store: SessionStore
    baseline_length: int
    event_seq: int = 0
    events: List[Dict[str, Any]] = field(default_factory=list)
    pending_run_end: Optional[Dict[str, Any]] = None


_running_ctx: Optional[RunContext] = None

# ====== 工具审批状态 ======
_pending_approvals: dict = {}   # tool_use_id -> approval metadata
_approval_lock = threading.Lock()
_approval_timeout_seconds = 120.0
_current_mode: str = "auto"     # plan | auto | manual


# 当本文件以 `python3 api_server.py` 直接运行时，模块名是 `__main__` 而非 `api_server`，
# 子模块里的 `import api_server` 会找不到已加载的模块而重新加载本文件，触发循环导入。
# 这里把 `api_server` 别名指向当前模块，确保子模块拿到同一份（全局状态已就绪的）模块对象。
import sys as _sys
_sys.modules.setdefault("api_server", _sys.modules[__name__])

# ====== 加载子模块（必须在全局状态定义之后，避免循环导入） ======
from api import session_helpers  # noqa: E402
from api import callbacks  # noqa: E402
from api import agent_core  # noqa: E402
from api import run_lifecycle  # noqa: E402
from api.routes.chat import chat_bp  # noqa: E402
from api.routes.sessions import sessions_bp  # noqa: E402
from api.routes.files import files_bp  # noqa: E402
from api.routes.models import models_bp  # noqa: E402
from api.routes.admin import admin_bp  # noqa: E402
from api import socket_handlers  # noqa: E402

# ====== 对外暴露的兼容接口（供 tests / 外部调用方使用 api_server.X 访问） ======
_ok = session_helpers._ok
_err = session_helpers._err
_require_agent = agent_core._require_agent
_store_key = session_helpers._store_key
_store_for_workdir = session_helpers._store_for_workdir
_resolve_session = session_helpers._resolve_session
_get_record = session_helpers._get_record
_make_title = session_helpers._make_title
_session_snapshot = session_helpers._session_snapshot
_should_approve = callbacks._should_approve
_push_event = callbacks._push_event
_push_run_event = callbacks._push_run_event
_emit_request_error = callbacks._emit_request_error
_approval_callback_for = callbacks._approval_callback_for
_approval_callback = callbacks._approval_callback
init_agent = agent_core.init_agent
ensure_agent_initialized = agent_core.ensure_agent_initialized
_build_system_prompt = agent_core._build_system_prompt
_persist_unfinished_stream = run_lifecycle._persist_unfinished_stream
_finish_run = run_lifecycle._finish_run
_execute_run = run_lifecycle._execute_run
_start_run = run_lifecycle._start_run


# ====== 注册 Blueprint 与 Socket 处理器 ======
app.register_blueprint(chat_bp)
app.register_blueprint(sessions_bp)
app.register_blueprint(files_bp)
app.register_blueprint(models_bp)
app.register_blueprint(admin_bp)
socket_handlers.register(socketio)


# ====== 启动 ======
def run_server(host="0.0.0.0", port=8000, debug=False):
    print("正在初始化 Agent...")
    ensure_agent_initialized()
    print(f"API 服务启动: http://{host}:{port}")
    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    run_server(debug=False)
