from enum import Enum
from typing import Callable, Optional

class PermissionMode(Enum):
    DEFAULT = "default"  # 默认(白名单自动，危险命令拦截)
    PLAN = "plan"        # 全部记录为计划，由用户统一确认
    AUTO = "auto"        # 全自动，不拦截(危险)
    BYPASS = "bypass"    # 特权模式(内部代理)

class SecurityArbiter:
    """
    4路权限竞争裁决机制 (基于 Claude Code 的裁决策略)。
    由 User(用户同意), Hook(钩子拦截), Classifier(风险分类器), Bridge(外部接管) 仲裁。
    """
    def __init__(self, mode: PermissionMode = PermissionMode.DEFAULT):
        self.mode = mode
        
    def check_tool_permission(self, tool_name: str, **kwargs) -> bool:
        """
        拦截和权限校验主入口
        """
        if self.mode == PermissionMode.AUTO or self.mode == PermissionMode.BYPASS:
            return True
            
        if self.mode == PermissionMode.PLAN:
            print(f"[🛡️ Security Plan] 被拦截并记录到计划清单中: {tool_name}({kwargs})")
            return False
            
        # DEFAULT 模式下：黑白名单及基于规则的检查
        if tool_name == "execute_command":
            cmd = kwargs.get("command", "")
            return self._is_safe_command(cmd)
            
        if tool_name == "write_file":
            filepath = kwargs.get("file_path", "")
            return self._is_safe_path(filepath)
            
        return True
        
    def _is_safe_command(self, cmd: str) -> bool:
        """简单的命令安全分类器实现"""
        dangerous_keywords = ["rm -rf", "mkfs", "dd", "> /dev/sda", "chown", "chmod 777"]
        for kw in dangerous_keywords:
            if kw in cmd:
                print(f"[🛡️ Security Audit] 危险命令被自动拦截: {cmd}")
                return False
        return True

    def _is_safe_path(self, filepath: str) -> bool:
        """防止越权/目录穿越攻击"""
        if ".." in filepath or filepath.startswith("/etc") or filepath.startswith("/root"):
            print(f"[🛡️ Security Audit] 检测到敏感路径读写尝试: {filepath}")
            return False
        return True
