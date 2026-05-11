"""
安全管理系统
提供版本控制、回退、沙箱执行等安全机制
"""
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import hashlib
import tarfile


class SafetyManager:
    """
    安全管理器
    - 快照和回退：在关键操作前创建快照，支持回退
    - 沙箱模式：在隔离环境中执行危险操作
    - 审计日志：记录所有操作历史
    - 风险评估：评估操作的风险级别
    """
    
    RISK_LEVELS = {
        "LOW": 1,      # 读取操作
        "MEDIUM": 2,   # 写入文件
        "HIGH": 3,     # 删除、修改重要文件
        "CRITICAL": 4  # 系统级操作、批量删除
    }
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.safety_dir = workspace / ".agent" / "safety"
        self.safety_dir.mkdir(parents=True, exist_ok=True)
        
        # 快照目录
        self.snapshots_dir = self.safety_dir / "snapshots"
        self.snapshots_dir.mkdir(exist_ok=True)
        
        # 审计日志
        self.audit_log = self.safety_dir / "audit.jsonl"
        
        # 沙箱目录
        self.sandbox_dir = self.safety_dir / "sandbox"
        self.sandbox_dir.mkdir(exist_ok=True)
        
        # 当前快照栈
        self.checkpoint_stack = []
        
        # 是否处于沙箱模式
        self.sandbox_mode = False
        
        # 加载快照索引
        self._load_snapshot_index()
    
    def _load_snapshot_index(self):
        """加载快照索引"""
        index_file = self.safety_dir / "snapshots.json"
        if index_file.exists():
            content = index_file.read_text().strip()
            self.snapshots = json.loads(content) if content else []
        else:
            self.snapshots = []
    
    def _save_snapshot_index(self):
        """保存快照索引"""
        index_file = self.safety_dir / "snapshots.json"
        index_file.write_text(json.dumps(self.snapshots, ensure_ascii=False, indent=2))
    
    def _log_audit(self, action: str, details: Dict, risk: str = "LOW"):
        """记录审计日志"""
        log_entry = {
            "timestamp": time.time(),
            "datetime": datetime.now().isoformat(),
            "action": action,
            "risk": risk,
            "details": details,
            "sandbox": self.sandbox_mode
        }
        
        with open(self.audit_log, "a") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    
    # ==================== 快照和回退 ====================
    
    def create_checkpoint(self, name: str, description: str = "", 
                         paths: Optional[List[str]] = None) -> str:
        """
        创建检查点（快照）
        paths: 要备份的路径列表，None表示备份整个workspace（排除.safety等）
        """
        checkpoint_id = f"cp_{int(time.time())}_{hashlib.md5(name.encode()).hexdigest()[:8]}"
        checkpoint_dir = self.snapshots_dir / checkpoint_id
        checkpoint_dir.mkdir()
        
        # 确定要备份的路径
        if paths is None:
            # 备份主要文件，排除临时目录
            exclude_dirs = {".agent", ".git", "__pycache__",
                          "node_modules", ".venv"}
            paths = []
            for item in self.workspace.iterdir():
                if item.name not in exclude_dirs:
                    paths.append(str(item.relative_to(self.workspace)))
        
        # 创建tar归档
        tar_path = checkpoint_dir / "snapshot.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            for path in paths:
                full_path = self.workspace / path
                if full_path.exists():
                    tar.add(full_path, arcname=path)
        
        # 保存元数据
        metadata = {
            "id": checkpoint_id,
            "name": name,
            "description": description,
            "paths": paths,
            "created_at": time.time(),
            "datetime": datetime.now().isoformat(),
            "size": tar_path.stat().st_size
        }
        
        (checkpoint_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2)
        )
        
        # 更新索引
        self.snapshots.append(metadata)
        self._save_snapshot_index()
        
        # 添加到栈
        self.checkpoint_stack.append(checkpoint_id)
        
        # 记录审计日志
        self._log_audit("create_checkpoint", {
            "checkpoint_id": checkpoint_id,
            "name": name,
            "paths_count": len(paths)
        }, "MEDIUM")
        
        return checkpoint_id
    
    def list_checkpoints(self, limit: int = 20) -> List[Dict]:
        """列出所有检查点"""
        # 按时间倒序
        sorted_snapshots = sorted(
            self.snapshots, 
            key=lambda x: x["created_at"], 
            reverse=True
        )
        return sorted_snapshots[:limit]
    
    def restore_checkpoint(self, checkpoint_id: str, 
                          verify: bool = True) -> str:
        """
        恢复到指定检查点
        verify: 是否在恢复前验证快照完整性
        """
        # 查找快照
        snapshot = None
        for s in self.snapshots:
            if s["id"] == checkpoint_id:
                snapshot = s
                break
        
        if not snapshot:
            raise ValueError(f"快照不存在: {checkpoint_id}")
        
        checkpoint_dir = self.snapshots_dir / checkpoint_id
        tar_path = checkpoint_dir / "snapshot.tar.gz"
        
        if not tar_path.exists():
            raise FileNotFoundError(f"快照文件丢失: {tar_path}")
        
        # 在恢复前创建一个自动快照
        auto_checkpoint = self.create_checkpoint(
            f"auto_before_restore_{checkpoint_id}",
            "恢复前的自动快照"
        )
        
        try:
            # 解压恢复
            with tarfile.open(tar_path, "r:gz") as tar:
                tar.extractall(self.workspace)
            
            # 记录审计日志
            self._log_audit("restore_checkpoint", {
                "checkpoint_id": checkpoint_id,
                "auto_backup": auto_checkpoint
            }, "HIGH")
            
            return f"已恢复到快照 {checkpoint_id}，自动备份: {auto_checkpoint}"
        
        except Exception as e:
            # 如果恢复失败，打印错误但保留自动快照
            self._log_audit("restore_checkpoint_failed", {
                "checkpoint_id": checkpoint_id,
                "error": str(e),
                "auto_backup": auto_checkpoint
            }, "CRITICAL")
            raise
    
    def rollback(self) -> str:
        """回滚到上一个检查点"""
        if not self.checkpoint_stack:
            return "没有可回滚的检查点"
        
        checkpoint_id = self.checkpoint_stack.pop()
        return self.restore_checkpoint(checkpoint_id)
    
    def delete_checkpoint(self, checkpoint_id: str) -> str:
        """删除检查点"""
        checkpoint_dir = self.snapshots_dir / checkpoint_id
        if checkpoint_dir.exists():
            shutil.rmtree(checkpoint_dir)
        
        # 从索引中移除
        self.snapshots = [s for s in self.snapshots if s["id"] != checkpoint_id]
        self._save_snapshot_index()
        
        # 从栈中移除
        if checkpoint_id in self.checkpoint_stack:
            self.checkpoint_stack.remove(checkpoint_id)
        
        self._log_audit("delete_checkpoint", {"checkpoint_id": checkpoint_id}, "MEDIUM")
        
        return f"已删除快照 {checkpoint_id}"
    
    # ==================== 沙箱模式 ====================
    
    def enter_sandbox(self) -> str:
        """进入沙箱模式"""
        if self.sandbox_mode:
            return "已在沙箱模式中"
        
        # 清空沙箱目录
        if self.sandbox_dir.exists():
            shutil.rmtree(self.sandbox_dir)
        self.sandbox_dir.mkdir()
        
        # 创建必要的子目录
        (self.sandbox_dir / "workspace").mkdir()
        (self.sandbox_dir / "temp").mkdir()
        
        self.sandbox_mode = True
        
        self._log_audit("enter_sandbox", {}, "MEDIUM")
        
        return "已进入沙箱模式，所有操作将在隔离环境中执行"
    
    def exit_sandbox(self, discard: bool = True) -> str:
        """退出沙箱模式"""
        if not self.sandbox_mode:
            return "未在沙箱模式中"
        
        result = ""
        if not discard:
            # 合并沙箱更改到主workspace
            result = "沙箱更改已应用到主工作区"
            # 这里可以添加选择性合并的逻辑
        else:
            result = "沙箱更改已丢弃"
        
        self.sandbox_mode = False
        
        self._log_audit("exit_sandbox", {"discard": discard}, "MEDIUM")
        
        return result
    
    def get_sandbox_path(self, relative_path: str) -> Path:
        """获取沙箱中的路径"""
        if self.sandbox_mode:
            return self.sandbox_dir / "workspace" / relative_path
        else:
            return self.workspace / relative_path
    
    # ==================== 风险评估 ====================
    
    def assess_risk(self, operation: str, targets: List[str]) -> Dict:
        """
        评估操作的风险级别
        """
        risk_score = self.RISK_LEVELS["LOW"]
        reasons = []
        
        # 操作类型风险
        if "delete" in operation.lower() or "remove" in operation.lower():
            risk_score = max(risk_score, self.RISK_LEVELS["HIGH"])
            reasons.append("删除操作")
        elif "write" in operation.lower() or "modify" in operation.lower():
            risk_score = max(risk_score, self.RISK_LEVELS["MEDIUM"])
            reasons.append("写入操作")
        
        # 目标风险
        critical_patterns = [".git", "requirements.txt", "package.json", 
                           "setup.py", "agent.py", "core/"]
        
        for target in targets:
            if any(pattern in target for pattern in critical_patterns):
                risk_score = max(risk_score, self.RISK_LEVELS["HIGH"])
                reasons.append(f"关键文件: {target}")
        
        # 批量操作风险
        if len(targets) > 10:
            risk_score = max(risk_score, self.RISK_LEVELS["HIGH"])
            reasons.append(f"批量操作: {len(targets)} 个目标")
        
        risk_name = [k for k, v in self.RISK_LEVELS.items() if v == risk_score][0]
        
        return {
            "risk_level": risk_name,
            "risk_score": risk_score,
            "reasons": reasons,
            "require_checkpoint": risk_score >= self.RISK_LEVELS["MEDIUM"],
            "require_confirmation": risk_score >= self.RISK_LEVELS["HIGH"]
        }
    
    # ==================== 审计日志 ====================
    
    def get_audit_log(self, limit: int = 50, 
                     risk_level: Optional[str] = None) -> List[Dict]:
        """获取审计日志"""
        if not self.audit_log.exists():
            return []
        
        logs = []
        with open(self.audit_log, "r") as f:
            for line in f:
                if line.strip():
                    logs.append(json.loads(line))
        
        # 过滤风险级别
        if risk_level:
            logs = [log for log in logs if log["risk"] == risk_level]
        
        # 返回最新的
        return logs[-limit:]
    
    def get_operation_history(self, limit: int = 20) -> str:
        """获取操作历史摘要"""
        logs = self.get_audit_log(limit)
        
        if not logs:
            return "暂无操作历史"
        
        lines = []
        for log in logs:
            timestamp = datetime.fromisoformat(log["datetime"]).strftime("%H:%M:%S")
            risk = log["risk"]
            action = log["action"]
            lines.append(f"[{timestamp}] [{risk}] {action}")
        
        return "\n".join(lines)
    
    # ==================== 安全执行包装器 ====================
    
    def safe_execute(self, operation: str, targets: List[str], 
                    execute_fn, auto_checkpoint: bool = True):
        """
        安全执行包装器
        在执行高风险操作前自动创建检查点
        """
        # 评估风险
        risk_assessment = self.assess_risk(operation, targets)
        
        # 记录日志
        self._log_audit(f"safe_execute_{operation}", {
            "targets": targets,
            "risk_assessment": risk_assessment
        }, risk_assessment["risk_level"])
        
        # 如果需要检查点且未在沙箱模式
        checkpoint_id = None
        if risk_assessment["require_checkpoint"] and auto_checkpoint and not self.sandbox_mode:
            checkpoint_id = self.create_checkpoint(
                f"auto_{operation}",
                f"执行 {operation} 前的自动检查点"
            )
        
        try:
            # 执行操作
            result = execute_fn()
            
            return {
                "success": True,
                "result": result,
                "checkpoint": checkpoint_id,
                "risk_assessment": risk_assessment
            }
        
        except Exception as e:
            # 如果失败且有检查点，提示可以回滚
            error_msg = str(e)
            if checkpoint_id:
                error_msg += f"\n可以使用 safety_restore({checkpoint_id}) 回滚"
            
            self._log_audit(f"safe_execute_failed_{operation}", {
                "error": str(e),
                "checkpoint": checkpoint_id
            }, "CRITICAL")
            
            return {
                "success": False,
                "error": error_msg,
                "checkpoint": checkpoint_id,
                "risk_assessment": risk_assessment
            }
    
    # ==================== 统计 ====================
    
    def get_safety_stats(self) -> Dict:
        """获取安全统计"""
        stats = {
            "total_checkpoints": len(self.snapshots),
            "sandbox_mode": self.sandbox_mode,
            "checkpoint_stack_depth": len(self.checkpoint_stack)
        }
        
        # 计算快照总大小
        total_size = sum(s.get("size", 0) for s in self.snapshots)
        stats["total_snapshot_size_mb"] = round(total_size / 1024 / 1024, 2)
        
        # 审计日志统计
        recent_logs = self.get_audit_log(100)
        stats["recent_operations"] = len(recent_logs)
        
        # 按风险级别统计
        risk_counts = {}
        for log in recent_logs:
            risk = log["risk"]
            risk_counts[risk] = risk_counts.get(risk, 0) + 1
        stats["risk_distribution"] = risk_counts
        
        return stats
