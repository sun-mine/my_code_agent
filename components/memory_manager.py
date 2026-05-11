"""
智能体记忆管理系统
支持短期会话记忆和长期持久化记忆
"""
import json
import time
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import sqlite3
from collections import defaultdict


class MemoryManager:
    """
    智能体记忆管理器
    - 短期记忆：当前会话，保存在内存中
    - 长期记忆：跨会话，持久化到SQLite数据库
    - 工作记忆：当前任务上下文
    """
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory_dir = workspace / ".agent" / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        
        # 数据库路径
        self.db_path = self.memory_dir / "longterm.db"
        
        # 短期记忆（当前会话）
        self.session_memory = {
            "context": {},  # 当前上下文
            "facts": [],    # 本次会话的事实
            "decisions": []  # 决策历史
        }
        
        # 工作记忆（当前任务）
        self.working_memory = {
            "task": None,
            "goal": None,
            "actions": [],
            "results": []
        }
        
        # 初始化长期记忆数据库
        self._init_db()
    
    def _init_db(self):
        """初始化SQLite数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 事实表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 0,
                UNIQUE(category, key)
            )
        """)
        
        # 经验表（成功/失败的模式）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                situation TEXT NOT NULL,
                action TEXT NOT NULL,
                outcome TEXT NOT NULL,
                success BOOLEAN NOT NULL,
                learned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                used_count INTEGER DEFAULT 0
            )
        """)
        
        # 技能记忆表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_name TEXT NOT NULL UNIQUE,
                usage_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                last_used TIMESTAMP,
                notes TEXT
            )
        """)
        
        # 上下文快照表（用于恢复会话）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS context_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                snapshot_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
    
    # ==================== 短期记忆 ====================
    
    def add_session_fact(self, fact: str, category: str = "general"):
        """添加会话事实"""
        self.session_memory["facts"].append({
            "fact": fact,
            "category": category,
            "timestamp": time.time()
        })
    
    def add_decision(self, decision: str, reasoning: str):
        """记录决策"""
        self.session_memory["decisions"].append({
            "decision": decision,
            "reasoning": reasoning,
            "timestamp": time.time()
        })
    
    def update_context(self, key: str, value: Any):
        """更新会话上下文"""
        self.session_memory["context"][key] = value
    
    def get_context(self, key: str) -> Any:
        """获取上下文"""
        return self.session_memory["context"].get(key)
    
    def get_session_summary(self) -> str:
        """获取会话摘要"""
        facts = self.session_memory["facts"]
        decisions = self.session_memory["decisions"]
        
        summary = []
        if facts:
            summary.append(f"本次会话记录了 {len(facts)} 个事实")
        if decisions:
            summary.append(f"做出了 {len(decisions)} 个决策")
        
        return "\n".join(summary) if summary else "会话为空"
    
    # ==================== 长期记忆 ====================
    
    def store_fact(self, category: str, key: str, value: str, 
                   confidence: float = 1.0, source: str = "agent"):
        """存储长期事实"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO facts (category, key, value, confidence, source)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(category, key) DO UPDATE SET
                value = excluded.value,
                confidence = excluded.confidence,
                source = excluded.source,
                accessed_at = CURRENT_TIMESTAMP,
                access_count = access_count + 1
        """, (category, key, value, confidence, source))
        
        conn.commit()
        conn.close()
    
    def recall_fact(self, category: str, key: str) -> Optional[str]:
        """回忆事实"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT value FROM facts 
            WHERE category = ? AND key = ?
        """, (category, key))
        
        row = cursor.fetchone()
        
        if row:
            # 更新访问记录
            cursor.execute("""
                UPDATE facts 
                SET accessed_at = CURRENT_TIMESTAMP, access_count = access_count + 1
                WHERE category = ? AND key = ?
            """, (category, key))
            conn.commit()
        
        conn.close()
        return row[0] if row else None
    
    def search_facts(self, category: Optional[str] = None, 
                     keyword: Optional[str] = None, limit: int = 10) -> List[Dict]:
        """搜索事实"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM facts WHERE 1=1"
        params = []
        
        if category:
            query += " AND category = ?"
            params.append(category)
        
        if keyword:
            query += " AND (key LIKE ? OR value LIKE ?)"
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        
        query += " ORDER BY accessed_at DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    # ==================== 经验学习 ====================
    
    def record_experience(self, situation: str, action: str, 
                         outcome: str, success: bool):
        """记录经验（成功或失败）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO experiences (situation, action, outcome, success)
            VALUES (?, ?, ?, ?)
        """, (situation, action, outcome, success))
        
        conn.commit()
        conn.close()
    
    def query_similar_experiences(self, situation: str, limit: int = 5) -> List[Dict]:
        """查询相似经验"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 简单的关键词匹配（可以升级为向量搜索）
        cursor.execute("""
            SELECT * FROM experiences 
            WHERE situation LIKE ?
            ORDER BY learned_at DESC
            LIMIT ?
        """, (f"%{situation}%", limit))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def get_success_patterns(self, limit: int = 10) -> List[Dict]:
        """获取成功模式"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT situation, action, COUNT(*) as count
            FROM experiences 
            WHERE success = 1
            GROUP BY situation, action
            ORDER BY count DESC
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    # ==================== 技能追踪 ====================
    
    def track_skill_usage(self, skill_name: str, success: bool, notes: str = ""):
        """追踪技能使用"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO skills (skill_name, usage_count, success_count, failure_count, last_used, notes)
            VALUES (?, 1, ?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(skill_name) DO UPDATE SET
                usage_count = usage_count + 1,
                success_count = success_count + ?,
                failure_count = failure_count + ?,
                last_used = CURRENT_TIMESTAMP,
                notes = CASE WHEN ? != '' THEN ? ELSE notes END
        """, (
            skill_name, 
            1 if success else 0, 
            0 if success else 1,
            notes,
            1 if success else 0,
            0 if success else 1,
            notes, notes
        ))
        
        conn.commit()
        conn.close()
    
    def get_skill_stats(self, skill_name: str) -> Optional[Dict]:
        """获取技能统计"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM skills WHERE skill_name = ?
        """, (skill_name,))
        
        row = cursor.fetchone()
        conn.close()
        
        return dict(row) if row else None
    
    # ==================== 工作记忆 ====================
    
    def set_current_task(self, task: str, goal: str):
        """设置当前任务"""
        self.working_memory["task"] = task
        self.working_memory["goal"] = goal
        self.working_memory["actions"] = []
        self.working_memory["results"] = []
    
    def add_action(self, action: str, result: str):
        """添加操作记录"""
        self.working_memory["actions"].append(action)
        self.working_memory["results"].append(result)
    
    def get_working_context(self) -> str:
        """获取工作上下文"""
        wm = self.working_memory
        if not wm["task"]:
            return "当前无活动任务"
        
        lines = [f"任务: {wm['task']}", f"目标: {wm['goal']}"]
        if wm["actions"]:
            lines.append(f"已执行 {len(wm['actions'])} 个操作")
        
        return "\n".join(lines)
    
    # ==================== 会话管理 ====================
    
    def save_session_snapshot(self, session_id: str):
        """保存会话快照"""
        snapshot = {
            "session_memory": self.session_memory,
            "working_memory": self.working_memory,
            "timestamp": time.time()
        }
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO context_snapshots (session_id, snapshot_data)
            VALUES (?, ?)
        """, (session_id, json.dumps(snapshot, ensure_ascii=False)))
        
        conn.commit()
        conn.close()
    
    def load_session_snapshot(self, session_id: str) -> bool:
        """加载会话快照"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT snapshot_data FROM context_snapshots
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (session_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            snapshot = json.loads(row[0])
            self.session_memory = snapshot["session_memory"]
            self.working_memory = snapshot["working_memory"]
            return True
        
        return False
    
    def clear_session(self):
        """清空会话记忆"""
        self.session_memory = {
            "context": {},
            "facts": [],
            "decisions": []
        }
        self.working_memory = {
            "task": None,
            "goal": None,
            "actions": [],
            "results": []
        }
    
    # ==================== 统计和报告 ====================
    
    def get_memory_stats(self) -> Dict:
        """获取记忆统计"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        stats = {}
        
        cursor.execute("SELECT COUNT(*) FROM facts")
        stats["total_facts"] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM experiences")
        stats["total_experiences"] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM experiences WHERE success = 1")
        stats["successful_experiences"] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM skills")
        stats["tracked_skills"] = cursor.fetchone()[0]
        
        conn.close()
        
        stats["session_facts"] = len(self.session_memory["facts"])
        stats["session_decisions"] = len(self.session_memory["decisions"])
        
        return stats
    
    def export_memory(self, output_path: Path):
        """导出记忆到JSON文件"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        export_data = {
            "facts": [],
            "experiences": [],
            "skills": []
        }
        
        cursor.execute("SELECT * FROM facts")
        export_data["facts"] = [dict(row) for row in cursor.fetchall()]
        
        cursor.execute("SELECT * FROM experiences")
        export_data["experiences"] = [dict(row) for row in cursor.fetchall()]
        
        cursor.execute("SELECT * FROM skills")
        export_data["skills"] = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        output_path.write_text(json.dumps(export_data, ensure_ascii=False, indent=2))
        
        return f"已导出 {len(export_data['facts'])} 个事实, {len(export_data['experiences'])} 个经验"
