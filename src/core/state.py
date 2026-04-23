from typing import Annotated, Sequence, TypedDict, List, Dict, Any
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    """
    LangGraph 的状态定义。在节点之间流转的数据载体。
    """
    # 消息历史，add_messages 确保新消息是追加而不是覆盖
    messages: Annotated[Sequence[BaseMessage], add_messages]
    
    # 路由节点决策的下一个执行节点
    next_node: str
    
    # 当前工作区上下文感知 (例如当前读取的文件树摘要)
    workspace_context: str
    
    # 当前任务的执行计划拆解
    active_plan: List[str]
    
    # 当前动态挂载的可复用技能列表
    dynamic_skills: List[str]
    
    # RAG / 记忆库检索到的相关经验片段
    retrieved_memories: List[Dict[str, Any]]
