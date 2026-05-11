"""
记忆工具
让智能体能够存储和检索记忆
"""

CATEGORY = "memory"

# 全局记忆管理器实例
_memory_mgr = None

def init_memory(memory_manager):
    """初始化记忆管理器"""
    global _memory_mgr
    _memory_mgr = memory_manager

def get_memory():
    """获取记忆管理器"""
    return _memory_mgr

MEMORY_TOOLS = [
    {
        "name": "memory_store",
        "description": "存储长期记忆事实。用于保存重要的项目信息、偏好、配置等。",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "事实分类（如：project, user_preference, configuration）"
                },
                "key": {
                    "type": "string",
                    "description": "事实的键名"
                },
                "value": {
                    "type": "string",
                    "description": "事实的值"
                },
                "confidence": {
                    "type": "number",
                    "description": "置信度 0-1"
                }
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "memory_recall",
        "description": "回忆特定的长期记忆事实。",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "事实分类"
                },
                "key": {
                    "type": "string",
                    "description": "事实的键名"
                }
            },
            "required": ["category", "key"]
        }
    },
    {
        "name": "memory_search",
        "description": "搜索记忆中的事实。",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "可选：限定搜索的分类"
                },
                "keyword": {
                    "type": "string",
                    "description": "可选：搜索关键词"
                },
                "limit": {
                    "type": "integer",
                    "description": "返回结果数量限制"
                }
            }
        }
    },
    {
        "name": "memory_experience",
        "description": "记录一次经验（成功或失败的操作）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "situation": {
                    "type": "string",
                    "description": "当时的情况描述"
                },
                "action": {
                    "type": "string",
                    "description": "采取的行动"
                },
                "outcome": {
                    "type": "string",
                    "description": "结果描述"
                },
                "success": {
                    "type": "boolean",
                    "description": "是否成功"
                }
            },
            "required": ["situation", "action", "outcome", "success"]
        }
    },
    {
        "name": "memory_query_experience",
        "description": "查询相似的历史经验。",
        "input_schema": {
            "type": "object",
            "properties": {
                "situation": {
                    "type": "string",
                    "description": "当前情况描述"
                },
                "limit": {
                    "type": "integer",
                    "description": "返回结果数量"
                }
            },
            "required": ["situation"]
        }
    },
    {
        "name": "memory_stats",
        "description": "获取记忆系统统计信息。",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "memory_session_context",
        "description": "获取当前会话上下文。",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "memory_working_context",
        "description": "获取当前工作记忆（任务上下文）。",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    }
]

def memory_store(**kwargs):
    """存储记忆"""
    mem = get_memory()
    if not mem:
        return "记忆系统未初始化"
    
    mem.store_fact(
        kwargs["category"],
        kwargs["key"],
        kwargs["value"],
        kwargs.get("confidence", 1.0)
    )
    return f"已存储: {kwargs['category']}.{kwargs['key']}"

def memory_recall(**kwargs):
    """回忆记忆"""
    mem = get_memory()
    if not mem:
        return "记忆系统未初始化"
    
    value = mem.recall_fact(kwargs["category"], kwargs["key"])
    if value:
        return f"{kwargs['key']}: {value}"
    return "未找到该记忆"

def memory_search(**kwargs):
    """搜索记忆"""
    mem = get_memory()
    if not mem:
        return "记忆系统未初始化"
    
    results = mem.search_facts(
        kwargs.get("category"),
        kwargs.get("keyword"),
        kwargs.get("limit", 10)
    )
    
    if not results:
        return "未找到匹配的记忆"
    
    lines = []
    for r in results:
        lines.append(f"[{r['category']}] {r['key']}: {r['value'][:100]}")
    
    return "\n".join(lines)

def memory_experience(**kwargs):
    """记录经验"""
    mem = get_memory()
    if not mem:
        return "记忆系统未初始化"
    
    mem.record_experience(
        kwargs["situation"],
        kwargs["action"],
        kwargs["outcome"],
        kwargs["success"]
    )
    
    status = "成功" if kwargs["success"] else "失败"
    return f"已记录{status}经验"

def memory_query_experience(**kwargs):
    """查询经验"""
    mem = get_memory()
    if not mem:
        return "记忆系统未初始化"
    
    experiences = mem.query_similar_experiences(
        kwargs["situation"],
        kwargs.get("limit", 5)
    )
    
    if not experiences:
        return "未找到相似经验"
    
    lines = []
    for exp in experiences:
        status = "✓" if exp["success"] else "✗"
        lines.append(f"{status} {exp['situation'][:50]} -> {exp['action'][:50]}")
    
    return "\n".join(lines)

def memory_stats(**kwargs):
    """记忆统计"""
    mem = get_memory()
    if not mem:
        return "记忆系统未初始化"
    
    stats = mem.get_memory_stats()
    
    lines = [
        f"长期记忆事实: {stats['total_facts']}",
        f"经验记录: {stats['total_experiences']} (成功: {stats['successful_experiences']})",
        f"技能追踪: {stats['tracked_skills']}",
        f"本次会话事实: {stats['session_facts']}",
        f"本次会话决策: {stats['session_decisions']}"
    ]
    
    return "\n".join(lines)

def memory_session_context(**kwargs):
    """会话上下文"""
    mem = get_memory()
    if not mem:
        return "记忆系统未初始化"
    
    return mem.get_session_summary()

def memory_working_context(**kwargs):
    """工作上下文"""
    mem = get_memory()
    if not mem:
        return "记忆系统未初始化"
    
    return mem.get_working_context()

MEMORY_HANDLERS = {
    "memory_store": memory_store,
    "memory_recall": memory_recall,
    "memory_search": memory_search,
    "memory_experience": memory_experience,
    "memory_query_experience": memory_query_experience,
    "memory_stats": memory_stats,
    "memory_session_context": memory_session_context,
    "memory_working_context": memory_working_context
}

def get_tools():
    return MEMORY_TOOLS

def create_handlers(**ctx):
    mem = ctx.get("memory")
    if mem:
        init_memory(mem)
    return dict(MEMORY_HANDLERS)
