"""
安全工具
让智能体能够使用安全机制
"""

CATEGORY = "safety"

# 全局安全管理器实例
_safety_mgr = None

def init_safety(safety_manager):
    """初始化安全管理器"""
    global _safety_mgr
    _safety_mgr = safety_manager

def get_safety():
    """获取安全管理器"""
    return _safety_mgr

SAFETY_TOOLS = [
    {
        "name": "safety_checkpoint",
        "description": "创建检查点（快照），在执行重要操作前备份。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "检查点名称"
                },
                "description": {
                    "type": "string",
                    "description": "检查点描述"
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要备份的路径列表，省略则备份主要文件"
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "safety_list_checkpoints",
        "description": "列出所有可用的检查点。",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "返回数量限制"
                }
            }
        }
    },
    {
        "name": "safety_restore",
        "description": "恢复到指定的检查点（回退）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "checkpoint_id": {
                    "type": "string",
                    "description": "检查点ID"
                }
            },
            "required": ["checkpoint_id"]
        }
    },
    {
        "name": "safety_rollback",
        "description": "回滚到上一个检查点。",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "safety_delete_checkpoint",
        "description": "删除指定的检查点。",
        "input_schema": {
            "type": "object",
            "properties": {
                "checkpoint_id": {
                    "type": "string",
                    "description": "检查点ID"
                }
            },
            "required": ["checkpoint_id"]
        }
    },
    {
        "name": "safety_enter_sandbox",
        "description": "进入沙箱模式，在隔离环境中执行操作。",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "safety_exit_sandbox",
        "description": "退出沙箱模式。",
        "input_schema": {
            "type": "object",
            "properties": {
                "discard": {
                    "type": "boolean",
                    "description": "是否丢弃沙箱中的更改"
                }
            }
        }
    },
    {
        "name": "safety_assess_risk",
        "description": "评估操作的风险级别。",
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "操作描述"
                },
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "目标文件列表"
                }
            },
            "required": ["operation", "targets"]
        }
    },
    {
        "name": "safety_audit_log",
        "description": "查看审计日志。",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "返回数量"
                },
                "risk_level": {
                    "type": "string",
                    "description": "过滤风险级别"
                }
            }
        }
    },
    {
        "name": "safety_history",
        "description": "获取操作历史摘要。",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "返回数量"
                }
            }
        }
    },
    {
        "name": "safety_stats",
        "description": "获取安全系统统计信息。",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    }
]

def safety_checkpoint(**kwargs):
    """创建检查点"""
    safety = get_safety()
    if not safety:
        return "安全系统未初始化"
    
    checkpoint_id = safety.create_checkpoint(
        kwargs["name"],
        kwargs.get("description", ""),
        kwargs.get("paths")
    )
    return f"已创建检查点: {checkpoint_id}"

def safety_list_checkpoints(**kwargs):
    """列出检查点"""
    safety = get_safety()
    if not safety:
        return "安全系统未初始化"
    
    checkpoints = safety.list_checkpoints(kwargs.get("limit", 20))
    
    if not checkpoints:
        return "暂无检查点"
    
    lines = []
    for cp in checkpoints:
        lines.append(f"[{cp['id']}] {cp['name']} - {cp['datetime']}")
        if cp.get('description'):
            lines.append(f"  描述: {cp['description']}")
    
    return "\n".join(lines)

def safety_restore(**kwargs):
    """恢复检查点"""
    safety = get_safety()
    if not safety:
        return "安全系统未初始化"
    
    try:
        result = safety.restore_checkpoint(kwargs["checkpoint_id"])
        return result
    except Exception as e:
        return f"恢复失败: {e}"

def safety_rollback(**kwargs):
    """回滚"""
    safety = get_safety()
    if not safety:
        return "安全系统未初始化"
    
    return safety.rollback()

def safety_delete_checkpoint(**kwargs):
    """删除检查点"""
    safety = get_safety()
    if not safety:
        return "安全系统未初始化"
    
    return safety.delete_checkpoint(kwargs["checkpoint_id"])

def safety_enter_sandbox(**kwargs):
    """进入沙箱"""
    safety = get_safety()
    if not safety:
        return "安全系统未初始化"
    
    return safety.enter_sandbox()

def safety_exit_sandbox(**kwargs):
    """退出沙箱"""
    safety = get_safety()
    if not safety:
        return "安全系统未初始化"
    
    return safety.exit_sandbox(kwargs.get("discard", True))

def safety_assess_risk(**kwargs):
    """评估风险"""
    safety = get_safety()
    if not safety:
        return "安全系统未初始化"
    
    assessment = safety.assess_risk(
        kwargs["operation"],
        kwargs["targets"]
    )
    
    lines = [
        f"风险级别: {assessment['risk_level']}",
        f"风险评分: {assessment['risk_score']}/4",
        f"需要检查点: {'是' if assessment['require_checkpoint'] else '否'}",
        f"需要确认: {'是' if assessment['require_confirmation'] else '否'}"
    ]
    
    if assessment['reasons']:
        lines.append("风险因素:")
        for reason in assessment['reasons']:
            lines.append(f"  - {reason}")
    
    return "\n".join(lines)

def safety_audit_log(**kwargs):
    """审计日志"""
    safety = get_safety()
    if not safety:
        return "安全系统未初始化"
    
    logs = safety.get_audit_log(
        kwargs.get("limit", 50),
        kwargs.get("risk_level")
    )
    
    if not logs:
        return "暂无审计日志"
    
    lines = []
    for log in logs:
        lines.append(f"[{log['datetime']}] [{log['risk']}] {log['action']}")
    
    return "\n".join(lines)

def safety_history(**kwargs):
    """操作历史"""
    safety = get_safety()
    if not safety:
        return "安全系统未初始化"
    
    return safety.get_operation_history(kwargs.get("limit", 20))

def safety_stats(**kwargs):
    """安全统计"""
    safety = get_safety()
    if not safety:
        return "安全系统未初始化"
    
    stats = safety.get_safety_stats()
    
    lines = [
        f"检查点总数: {stats['total_checkpoints']}",
        f"快照总大小: {stats['total_snapshot_size_mb']} MB",
        f"沙箱模式: {'是' if stats['sandbox_mode'] else '否'}",
        f"检查点栈深度: {stats['checkpoint_stack_depth']}",
        f"近期操作数: {stats['recent_operations']}"
    ]
    
    if stats.get('risk_distribution'):
        lines.append("风险分布:")
        for risk, count in stats['risk_distribution'].items():
            lines.append(f"  {risk}: {count}")
    
    return "\n".join(lines)

SAFETY_HANDLERS = {
    "safety_checkpoint": safety_checkpoint,
    "safety_list_checkpoints": safety_list_checkpoints,
    "safety_restore": safety_restore,
    "safety_rollback": safety_rollback,
    "safety_delete_checkpoint": safety_delete_checkpoint,
    "safety_enter_sandbox": safety_enter_sandbox,
    "safety_exit_sandbox": safety_exit_sandbox,
    "safety_assess_risk": safety_assess_risk,
    "safety_audit_log": safety_audit_log,
    "safety_history": safety_history,
    "safety_stats": safety_stats
}

def get_tools():
    return SAFETY_TOOLS

def create_handlers(**ctx):
    s = ctx.get("safety")
    if s:
        init_safety(s)
    return dict(SAFETY_HANDLERS)
