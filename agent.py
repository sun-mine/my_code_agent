
import json
import os
import time
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

from core.loop import AgentLoop
from components.config import Config
from components.compactor import auto_compact
from components.todo_manager import TodoManager
from components.skill_loader import SkillLoader
from components.task_manager import TaskManager
from components.message_bus import MessageBus
from components.team_manager import TeammateManager
from components.background_manager import BackgroundManager
from components.memory_manager import MemoryManager
from components.safety_manager import SafetyManager
from components.error_recovery import ErrorRecoveryManager, init_error_recovery
from tools import get_all_tools, create_all_handlers
# 在 agent.py 中，创建 handlers 后添加：
from core.subagent import run_subagent

def main():
    cfg = Config()
    client = Anthropic(
        api_key=os.getenv("ANTHROPIC_AUTH_TOKEN"),
        base_url=os.getenv("ANTHROPIC_BASE_URL")
        )
    model = os.environ["MODEL_ID"]
    
    # 初始化基础组件
    skills = SkillLoader(cfg.skills_dir)
    bus = MessageBus(cfg.team_dir / "inbox")
    task_mgr = TaskManager(cfg.tasks_dir)
    bg = BackgroundManager()
    team = TeammateManager(bus, task_mgr, client, model, cfg)
    todo = TodoManager()
    
    # 初始化新组件：记忆、安全、错误恢复
    memory = MemoryManager(cfg.workdir)
    safety = SafetyManager(cfg.workdir)
    error_recovery = ErrorRecoveryManager(cfg.workdir, memory, safety)
    init_error_recovery(error_recovery)
    
    # 启动提示：创建初始检查点
    print("创建初始检查点...")
    initial_checkpoint = safety.create_checkpoint("session_start", "会话启动时的初始快照")
    print(f"初始检查点: {initial_checkpoint}")

    # 将组件传入工具注册表（自动发现）
    tools = get_all_tools()
    handlers = create_all_handlers(
        todo_manager=todo,
        skill_loader=skills,
        task_manager=task_mgr,
        message_bus=bus,
        team_manager=team,
        background=bg,
        config=cfg,
        memory=memory,
        safety=safety,
        compactor=None,
    )
    handlers["task"] = lambda **kw: run_subagent(client, model, kw["prompt"], kw.get("agent_type", "Explore"))
    
    # 自动生成工具列表描述
    tool_names = [t["name"] for t in tools]
    system = f"""You are a coding agent at {cfg.workdir}. Use tools to solve tasks.
Available tools: {', '.join(tool_names)}.
Prefer task_create/task_update/task_list for multi-step work.
Use TodoWrite for short checklists.
Use task for subagent delegation.
Use load_skill for specialized knowledge.

Skills available:
{skills.descriptions()}"""

    agent = AgentLoop(
        client=client,
        model=model,
        system=system,
        tools=tools,
        tool_handlers=handlers,
        bg=bg,
        bus=bus,
        todo_manager=todo,
        token_threshold=cfg.token_threshold
    )

    print(f"Agent started at {cfg.workdir}")
    print("记忆系统、安全机制已启用")
    print("输入 /help 查看命令")
    history = []
    while True:
        try:
            query = input("\033[36ms_full >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            # 保存会话快照
            session_id = f"session_{int(time.time())}"
            memory.save_session_snapshot(session_id)
            print(f"会话已保存: {session_id}")
            break
        if query.strip() == "/compact":
            if history:
                history[:] = auto_compact(client, model, history)
            continue
        if query.strip() == "/tasks":
            print(task_mgr.list_all())
            continue
        if query.strip() == "/team":
            print(team.list_all())
            continue
        if query.strip() == "/inbox":
            print(json.dumps(bus.read_inbox("lead"), indent=2))
            continue
        if query.strip() == "/memory":
            stats = memory.get_memory_stats()
            print(json.dumps(stats, indent=2, ensure_ascii=False))
            continue
        if query.strip() == "/safety":
            stats = safety.get_safety_stats()
            print(json.dumps(stats, indent=2, ensure_ascii=False))
            continue
        if query.strip() == "/checkpoints":
            checkpoints = safety.list_checkpoints(10)
            for cp in checkpoints:
                print(f"[{cp['id']}] {cp['name']} - {cp['datetime']}")
            continue
        if query.strip() == "/errors":
            print(error_recovery.analyze_error_pattern())
            continue
        if query.strip() == "/help":
            print("""
可用命令:
  /compact      - 压缩对话历史
  /tasks        - 查看任务列表
  /team         - 查看团队成员
  /inbox        - 查看收件箱
  /memory       - 查看记忆统计
  /safety       - 查看安全统计
  /checkpoints  - 查看检查点列表
  /errors       - 查看错误分析
  /help         - 显示帮助
  q/exit        - 退出
            """)
            continue
        history.append({"role": "user", "content": query})
        
        try:
            agent.run(history)
        except Exception as e:
            # 使用错误恢复系统处理未捕获的错误
            error_info = error_recovery.handle_error(e, {"operation": "agent_run"})
            print(f"\n错误: {error_info['message']}")
            print("\n恢复建议:")
            for i, suggestion in enumerate(error_info['suggestions'], 1):
                print(f"  {i}. {suggestion}")
        
        # 打印最后一条回复文本
        last_msg = history[-1]
        if isinstance(last_msg.get("content"), list):
            for block in last_msg["content"]:
                if hasattr(block, "text"):
                    print(block.text)
        print()

if __name__ == "__main__":
    main()