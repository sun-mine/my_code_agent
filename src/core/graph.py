from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver
from src.core.state import AgentState
from src.models.factory import get_model
from src.tools.filesys import write_file, read_file, replace_in_file
from src.tools.terminal import execute_command, get_background_status

# Define the tools
tools = [write_file, read_file, replace_in_file, execute_command, get_background_status]
tool_node = ToolNode(tools)

def supervisor_node(state: AgentState):
    """
    中枢监控节点 (Router): 负责分析用户意图，并将任务路由给专业节点。
    """
    print("[Router] 正在分析任务意图...")
    messages = state.get("messages", [])
    if not messages:
        return {"next_node": END}
        
    last_user_message = messages[-1].content
    
    # 这里应该用 LLM (DeepSeek-reasoner) 来做意图分类
    # 为保证系统稳定，我们先尝试加载模型进行路由检查
    try:
        model = get_model(model_type="glm") # 用 GLM flash 做快速路由分析
        prompt = f"""
分析以下用户的输入，并输出你应该将任务路由到哪个节点：
只允许输出以下三个单词之一：
- "coder": 如果用户要求写代码、修复bug、改写脚本、或者涉及代码相关的具体技术任务。
- "debugger": 如果用户粘贴了报错信息（Traceback、日志等），或明确要求排查报错。
- "chat": 如果用户仅仅是日常打招呼、询问"你是谁"、"你能做什么"等通用问答。

用户输入: {last_user_message}
输出:"""
        route_decision = model.invoke([HumanMessage(content=prompt)]).content.strip().lower()
        
        if "coder" in route_decision:
            next_node = "coder"
        elif "debugger" in route_decision:
            next_node = "debugger"
        else:
            next_node = "chat"
    except Exception as e:
        print(f"[Router] 模型路由异常，降级为默认策略: {e}")
        # 如果没有配置API Key等，降级到一个默认规则
        if "干什么" in last_user_message or "能做" in last_user_message:
            next_node = "chat"
        else:
            next_node = "coder"

    return {"next_node": next_node}

def chat_node(state: AgentState):
    """
    日常闲聊与自我介绍节点
    """
    print("[Chat] 正在响应日常交互...")
    try:
        model = get_model("chat")
        system_prompt = SystemMessage(content="你是基于 LangGraph 构建的 AI 代码智能体助手。你可以编写代码、执行工具、调试报错等。请用友好、专业的态度回答用户，如果用户问你能干什么，请介绍你的核心能力。")
        response = model.invoke([system_prompt] + list(state["messages"]))
        msg = response
    except Exception as e:
        msg = AIMessage(content=f"系统提示：请复制一份 `.env.example` 到 `.env` 文件并填入正确的 API KEY 才能激活实际对话。\n*(模型异常详情: {e})*")
        
    return {"messages": [msg], "next_node": END}

def coder_node(state: AgentState):
    """
    编码节点: 负责代码生成和文件修改。
    """
    print("[Coder] 正在处理代码生成任务...")
    
    try:
        model = get_model("chat") # 实际情况可配置为 doubao-code 或 deepseek-chat
        model_with_tools = model.bind_tools(tools)
        system_prompt = SystemMessage(
            content="你是一个专业的代码编写专家(Coder Node)。\n"
                    "重要规则：\n"
                    "1. 绝不要为一个需求提供多种语言或版本的实现（例如不要同时写网页版和Python版），一次只给出一个最符合要求的最优版本即可！\n"
                    "2. 如果用户要求用命令行或不适用某种语言，请严格遵守（如写 bash 脚本实现）。\n"
                    "3. 如果需要调试或修改代码，必须直接修补并覆盖原来的文件！绝不允许像新建 `test2.py`、`main_stable.py` 这种重新创建无数个新文件。要始终在同一个文件上修改迭代！\n"
                    "4. 如果需要运行或测试，请使用工具执行。不要瞎猜执行结果。\n"
                    "5. 严格遵守极简交付规则：用户要什么你就只提供什么！绝不要擅作主张生成辅助测试脚本、README文档、安装指南等。完成用户的单点核心诉求后，立即停止产生后续无用工作量。"
        )
        response = model_with_tools.invoke([system_prompt] + list(state["messages"]))
        msg = response
    except Exception as e:
        msg = AIMessage(content=f"代码分析完毕，但需要配置 `.env` 才能得到真正的 LLM 回复。*(异常: {e})*")
        
    return {"messages": [msg], "next_node": END}

def debugger_node(state: AgentState):
    """
    诊断分析节点: 分析终端日志或多模态截图。
    """
    print("[Debugger] 正在分析报错信息...")
    return {"next_node": "coder"}

def build_graph():
    """
    构建并编译 LangGraph 状态图
    """
    workflow = StateGraph(AgentState)

    # 注册节点
    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("chat", chat_node)
    workflow.add_node("coder", coder_node)
    workflow.add_node("debugger", debugger_node)
    workflow.add_node("tools", tool_node)

    # 制定路由规则
    workflow.set_entry_point("supervisor")
    
    # 条件边：根据 supervisor 输出的 next_node 决定去向
    workflow.add_conditional_edges(
        "supervisor",
        lambda x: x["next_node"],
        {
            "coder": "coder",
            "debugger": "debugger",
            "chat": "chat",
            END: END
        }
    )
    
    workflow.add_conditional_edges(
        "coder",
        tools_condition,
        {
            "tools": "tools",
            END: END
        }
    )
    
    workflow.add_edge("chat", END)
    workflow.add_edge("debugger", "coder")
    workflow.add_edge("tools", "coder")

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)
