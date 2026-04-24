from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver
from src.core.state import AgentState
from src.models.factory import get_model
from src.tools.filesys import write_file, read_file, replace_in_file
from src.tools.terminal import execute_command, get_background_status
from src.tools.knowledge import search_web
from src.tools.memory_tools import remember, recall
from src.utils.prompt_manager import load_prompt

# Define the tools
code_tools = [write_file, read_file, replace_in_file, execute_command, get_background_status, search_web, remember, recall]
explore_tools = [read_file, execute_command, get_background_status, search_web, remember, recall]
chat_tools = [search_web]

coder_tool_node = ToolNode(code_tools)
planner_tool_node = ToolNode(explore_tools)
chat_tool_node = ToolNode(chat_tools)
debugger_tool_node = ToolNode(explore_tools)

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
        model = get_model(model_type="deepseek-reasoner") # 意图分析使用 deepseek-reasoner
        supervisor_prompt_tmpl = load_prompt("supervisor")
        prompt = supervisor_prompt_tmpl.format(user_input=last_user_message)
        route_decision = model.invoke([HumanMessage(content=prompt)]).content.strip().lower()
        
        if "coder" in route_decision:
            next_node = "planner"
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
            next_node = "planner"

    return {"next_node": next_node}

def chat_node(state: AgentState):
    """
    日常闲聊与自我介绍节点
    """
    print("[Chat] 正在响应日常交互...")
    try:
        model = get_model("glm") # chat 使用 GLM
        model_with_tools = model.bind_tools(chat_tools)
        system_prompt = SystemMessage(content=load_prompt("chat"))
        response = model_with_tools.invoke([system_prompt] + list(state["messages"]))
        msg = response
    except Exception as e:
        msg = AIMessage(content=f"系统提示：请复制一份 `.env.example` 到 `.env` 文件并填入正确的 API KEY 才能激活实际对话。\n*(模型异常详情: {e})*")
        
    return {"messages": [msg]}


def planner_node(state: AgentState):
    '''
    规划节点: 在采取行动之前，主动使用工具收集上下文，然后制定清晰的拆解步骤。
    '''
    print("[Planner] 正在收集上下文并生成计划...")
    
    try:
        model = get_model("deepseek-chat") # planner 使用 deepseek-chat
        model_with_tools = model.bind_tools(explore_tools)
        system_prompt = SystemMessage(content=load_prompt("planner"))
        response = model_with_tools.invoke([system_prompt] + list(state["messages"]))
        msg = response
    except Exception as e:
        msg = AIMessage(content=f"[Planner] 规划异常: {e}")
        
    return {"messages": [msg], "next_node": "coder"}

def coder_node(state: AgentState):
    """
    编码节点: 负责代码生成和文件修改。
    """
    print("[Coder] 正在处理代码生成任务...")
    
    try:
        model = get_model("doubao") # coder 使用 豆包2.0code
        model_with_tools = model.bind_tools(code_tools)
        system_prompt = SystemMessage(content=load_prompt("coder"))
        response = model_with_tools.invoke([system_prompt] + list(state["messages"]))
        msg = response
    except Exception as e:
        msg = AIMessage(content=f"代码分析完毕，但需要配置 `.env` 才能得到真正的 LLM 回复。*(异常: {e})*")
        print(f"[Coder 异常]: {e}")
        
    return {"messages": [msg], "next_node": END}

def debugger_node(state: AgentState):
    """
    诊断分析节点: 分析终端日志或多模态截图。
    """
    print("[Debugger] 正在分析报错信息...")
    try:
        model = get_model("doubao") # debugger 使用 豆包2.0code
        model_with_tools = model.bind_tools(explore_tools)  # 可以配一些文件查阅工具
        system_prompt = SystemMessage(content=load_prompt("debugger"))
        response = model_with_tools.invoke([system_prompt] + list(state["messages"]))
        msg = response
    except Exception as e:
        msg = AIMessage(content=f"[Debugger] 诊断异常: {e}")
        
    return {"messages": [msg], "next_node": "coder"}

def build_graph():
    """
    构建并编译 LangGraph 状态图
    """
    workflow = StateGraph(AgentState)

    # 注册节点
    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("chat", chat_node)
    workflow.add_node("coder", coder_node)
    workflow.add_node("debugger", debugger_node)
    workflow.add_node("coder_tools", coder_tool_node)
    workflow.add_node("planner_tools", planner_tool_node)

    workflow.add_node("chat_tools", chat_tool_node)
    workflow.add_node("debugger_tools", debugger_tool_node)

    # 制定路由规则
    workflow.set_entry_point("supervisor")
    
    # 条件边：根据 supervisor 输出的 next_node 决定去向
    workflow.add_conditional_edges(
        "supervisor",
        lambda x: x["next_node"],
        {
            "planner": "planner",
            "debugger": "debugger",
            "chat": "chat",
            END: END
        }
    )
    
    # Planner 的工具调用循环
    workflow.add_conditional_edges(
        "planner",
        tools_condition,
        {
            "tools": "planner_tools",
            END: "coder"
        }
    )
    workflow.add_edge("planner_tools", "planner")
    
    # Coder 的工具调用循环
    workflow.add_conditional_edges(
        "coder",
        tools_condition,
        {
            "tools": "coder_tools",
            END: END
        }
    )
    workflow.add_edge("coder_tools", "coder")
    
    # Chat 的工具调用循环
    workflow.add_conditional_edges(
        "chat",
        tools_condition,
        {
            "tools": "chat_tools",
            END: END
        }
    )
    workflow.add_edge("chat_tools", "chat")
    
    # Debugger 的工具调用循环
    workflow.add_conditional_edges(
        "debugger",
        tools_condition,
        {
            "tools": "debugger_tools",
            END: "coder"
        }
    )
    workflow.add_edge("debugger_tools", "debugger")

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)
