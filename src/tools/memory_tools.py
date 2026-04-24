from langchain_core.tools import tool
from src.memory.manager import MemoryManager

def get_memory_manager() -> MemoryManager:
    # 假设都在工作区根目录下运行
    import os
    return MemoryManager(os.getcwd())

@tool
def remember(scope: str, key: str, value: str) -> str:
    """
    保存长期的知识或结论到持久化记忆库中。
    :param scope: 记忆的作用域，只能是 'global', 'session', 'repo' 之一。
    :param key: 记忆的名称（唯一简短的英文名）。
    :param value: 要保存的内容。
    """
    if scope not in ["global", "session", "repo"]:
        return "保存失败，scope 只能是 global, session 或 repo。"
    
    manager = get_memory_manager()
    manager.write_memory(scope, key, value)
    return f"✅ 成功将 '{key}' 保存到 {scope} 记忆库中。"

@tool
def recall(scope: str, key: str) -> str:
    """
    从持久化记忆库中检索特定的知识。
    """
    if scope not in ["global", "session", "repo"]:
        return "检索失败，scope 只能是 global, session 或 repo。"
        
    manager = get_memory_manager()
    val = manager.read_memory(scope, key)
    if val:
        return f"📖 {scope} 记忆 '{key}':\n{val}"
    return f"找不到 {scope} 范围内关于 '{key}' 的记忆。"
