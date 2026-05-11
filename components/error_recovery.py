"""
错误处理和恢复系统
提供智能的错误捕获、分析和恢复建议
"""
import json
import traceback
import time
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime
from functools import wraps
import re


class ErrorRecoveryManager:
    """
    错误恢复管理器
    - 捕获和记录错误
    - 分析错误原因
    - 提供恢复建议
    - 自动重试机制
    """
    
    ERROR_CATEGORIES = {
        "file_not_found": "文件未找到",
        "permission_denied": "权限拒绝",
        "syntax_error": "语法错误",
        "import_error": "导入错误",
        "api_error": "API错误",
        "network_error": "网络错误",
        "timeout": "超时",
        "memory_error": "内存错误",
        "unknown": "未知错误"
    }
    
    def __init__(self, workspace: Path, memory_manager=None, safety_manager=None):
        self.workspace = workspace
        self.memory = memory_manager
        self.safety = safety_manager
        
        self.error_dir = workspace / ".errors"
        self.error_dir.mkdir(exist_ok=True)
        
        # 错误日志
        self.error_log = self.error_dir / "errors.jsonl"
        
        # 恢复策略
        self.recovery_strategies = {}
        self._init_recovery_strategies()
        
        # 错误计数器
        self.error_counts = {}
    
    def _init_recovery_strategies(self):
        """初始化恢复策略"""
        self.recovery_strategies = {
            "file_not_found": [
                "检查文件路径是否正确",
                "确认文件是否已被删除或移动",
                "使用 safety_list_checkpoints 查看是否有可恢复的版本",
                "检查是否在正确的工作目录"
            ],
            "permission_denied": [
                "检查文件权限",
                "确认用户是否有访问权限",
                "尝试使用 sudo（如果适用）",
                "检查文件是否被其他进程锁定"
            ],
            "syntax_error": [
                "检查语法是否正确",
                "查看错误提示的行号",
                "确认使用的语法版本",
                "使用 lint 工具检查"
            ],
            "import_error": [
                "检查依赖是否已安装",
                "确认包名是否正确",
                "检查 Python 环境",
                "尝试 pip install <package>"
            ],
            "api_error": [
                "检查 API 密钥是否正确",
                "确认网络连接",
                "检查 API 限流",
                "查看 API 文档"
            ],
            "timeout": [
                "增加超时时间",
                "检查网络连接",
                "分解为更小的操作",
                "检查系统资源"
            ],
            "memory_error": [
                "减少处理的数据量",
                "使用流式处理",
                "增加系统内存",
                "优化算法"
            ]
        }
    
    def _categorize_error(self, error: Exception) -> str:
        """分类错误"""
        error_type = type(error).__name__
        error_msg = str(error).lower()
        
        if "FileNotFoundError" in error_type or "no such file" in error_msg:
            return "file_not_found"
        elif "PermissionError" in error_type or "permission denied" in error_msg:
            return "permission_denied"
        elif "SyntaxError" in error_type:
            return "syntax_error"
        elif "ImportError" in error_type or "ModuleNotFoundError" in error_type:
            return "import_error"
        elif "APIError" in error_type or "api" in error_msg:
            return "api_error"
        elif "timeout" in error_msg:
            return "timeout"
        elif "MemoryError" in error_type or "memory" in error_msg:
            return "memory_error"
        else:
            return "unknown"
    
    def _log_error(self, error: Exception, context: Dict):
        """记录错误"""
        category = self._categorize_error(error)
        
        error_entry = {
            "timestamp": time.time(),
            "datetime": datetime.now().isoformat(),
            "category": category,
            "type": type(error).__name__,
            "message": str(error),
            "traceback": traceback.format_exc(),
            "context": context
        }
        
        with open(self.error_log, "a") as f:
            f.write(json.dumps(error_entry, ensure_ascii=False) + "\n")
        
        # 更新错误计数
        self.error_counts[category] = self.error_counts.get(category, 0) + 1
        
        # 如果有记忆系统，记录经验
        if self.memory:
            self.memory.record_experience(
                situation=context.get("operation", "unknown"),
                action=context.get("action", "unknown"),
                outcome=str(error),
                success=False
            )
        
        return error_entry
    
    def get_recovery_suggestions(self, error: Exception, context: Dict) -> List[str]:
        """获取恢复建议"""
        category = self._categorize_error(error)
        
        suggestions = self.recovery_strategies.get(category, ["检查错误信息", "查看日志"])
        
        # 如果有安全管理器且有检查点，添加回滚建议
        if self.safety and hasattr(self.safety, 'checkpoint_stack') and self.safety.checkpoint_stack:
            suggestions.insert(0, "可以使用 safety_rollback 回滚到上一个检查点")
        
        # 如果有记忆系统，查询类似的成功经验
        if self.memory:
            similar_experiences = self.memory.query_similar_experiences(
                context.get("operation", ""),
                limit=3
            )
            
            success_experiences = [exp for exp in similar_experiences if exp.get("success")]
            if success_experiences:
                suggestions.append("以下是之前成功的类似操作:")
                for exp in success_experiences[:2]:
                    suggestions.append(f"  - {exp.get('action', '')[:100]}")
        
        return suggestions
    
    def handle_error(self, error: Exception, context: Dict) -> Dict:
        """
        处理错误
        返回错误信息和恢复建议
        """
        # 记录错误
        error_entry = self._log_error(error, context)
        
        # 获取恢复建议
        suggestions = self.get_recovery_suggestions(error, context)
        
        return {
            "category": error_entry["category"],
            "type": error_entry["type"],
            "message": error_entry["message"],
            "suggestions": suggestions,
            "logged": True
        }
    
    def error_handler(self, operation: str, **context):
        """
        装饰器：自动处理错误
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*args, **kwargs):
                ctx = {"operation": operation, **context}
                
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    error_info = self.handle_error(e, ctx)
                    
                    # 格式化错误消息
                    msg = f"错误 [{error_info['category']}]: {error_info['message']}\n"
                    msg += "\n恢复建议:\n"
                    msg += "\n".join(f"  {i+1}. {s}" for i, s in enumerate(error_info['suggestions']))
                    
                    return msg
            
            return wrapper
        return decorator
    
    def safe_retry(self, func: Callable, max_retries: int = 3, 
                   delay: float = 1.0, backoff: float = 2.0) -> Any:
        """
        安全重试机制
        """
        last_error = None
        
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(delay * (backoff ** attempt))
                    continue
                else:
                    # 最后一次失败，处理错误
                    return self.handle_error(e, {
                        "operation": f"retry_{func.__name__}",
                        "attempts": max_retries
                    })
    
    def get_error_history(self, limit: int = 50, 
                         category: Optional[str] = None) -> List[Dict]:
        """获取错误历史"""
        if not self.error_log.exists():
            return []
        
        errors = []
        with open(self.error_log, "r") as f:
            for line in f:
                if line.strip():
                    errors.append(json.loads(line))
        
        # 过滤分类
        if category:
            errors = [e for e in errors if e["category"] == category]
        
        # 返回最新的
        return errors[-limit:]
    
    def get_error_stats(self) -> Dict:
        """获取错误统计"""
        errors = self.get_error_history(1000)
        
        stats = {
            "total_errors": len(errors),
            "by_category": {},
            "by_type": {},
            "recent_errors": len([e for e in errors if time.time() - e["timestamp"] < 3600])
        }
        
        for error in errors:
            cat = error["category"]
            typ = error["type"]
            
            stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1
            stats["by_type"][typ] = stats["by_type"].get(typ, 0) + 1
        
        return stats
    
    def get_frequent_errors(self, limit: int = 5) -> List[Dict]:
        """获取频繁错误"""
        errors = self.get_error_history(1000)
        
        # 按错误消息分组
        error_groups = {}
        for error in errors:
            msg = error["message"][:100]  # 截断长消息
            if msg not in error_groups:
                error_groups[msg] = {
                    "message": msg,
                    "count": 0,
                    "category": error["category"],
                    "last_seen": error["datetime"]
                }
            error_groups[msg]["count"] += 1
        
        # 排序
        sorted_errors = sorted(
            error_groups.values(),
            key=lambda x: x["count"],
            reverse=True
        )
        
        return sorted_errors[:limit]
    
    def analyze_error_pattern(self) -> str:
        """分析错误模式"""
        stats = self.get_error_stats()
        frequent = self.get_frequent_errors(3)
        
        lines = [
            "错误分析报告",
            "=" * 50,
            f"总错误数: {stats['total_errors']}",
            f"近一小时错误: {stats['recent_errors']}",
            "",
            "按类别统计:"
        ]
        
        for cat, count in sorted(stats["by_category"].items(), key=lambda x: x[1], reverse=True):
            cat_name = self.ERROR_CATEGORIES.get(cat, cat)
            lines.append(f"  {cat_name}: {count}")
        
        if frequent:
            lines.append("")
            lines.append("最频繁错误:")
            for i, err in enumerate(frequent, 1):
                lines.append(f"  {i}. [{err['category']}] {err['message']} (出现{err['count']}次)")
        
        return "\n".join(lines)
    
    def clear_old_errors(self, days: int = 7):
        """清理旧错误日志"""
        if not self.error_log.exists():
            return "无错误日志"
        
        cutoff_time = time.time() - (days * 24 * 3600)
        
        errors = []
        with open(self.error_log, "r") as f:
            for line in f:
                if line.strip():
                    error = json.loads(line)
                    if error["timestamp"] >= cutoff_time:
                        errors.append(error)
        
        # 重写日志文件
        with open(self.error_log, "w") as f:
            for error in errors:
                f.write(json.dumps(error, ensure_ascii=False) + "\n")
        
        removed = len(self.get_error_history(10000)) - len(errors)
        return f"已清理 {removed} 条旧错误记录"


# 全局错误管理器
_error_mgr = None

def init_error_recovery(error_manager):
    """初始化错误管理器"""
    global _error_mgr
    _error_mgr = error_manager

def get_error_recovery():
    """获取错误管理器"""
    return _error_mgr

def handle_error(error: Exception, context: Dict) -> Dict:
    """处理错误的便捷函数"""
    mgr = get_error_recovery()
    if mgr:
        return mgr.handle_error(error, context)
    else:
        return {
            "category": "unknown",
            "type": type(error).__name__,
            "message": str(error),
            "suggestions": ["错误管理系统未初始化"]
        }
