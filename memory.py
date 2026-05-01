import sqlite3
import json
import time
from datetime import datetime
from config import DB_PATH, MAX_CONTEXT_MESSAGES


class Memory:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                sender_id TEXT DEFAULT '',
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL DEFAULT 'global',
                rule TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                open_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS long_term_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL DEFAULT 'global',
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, key)
            );

            CREATE INDEX IF NOT EXISTS idx_conv_chat ON conversations(chat_id);
            CREATE INDEX IF NOT EXISTS idx_conv_sender ON conversations(sender_id);
            CREATE INDEX IF NOT EXISTS idx_rules_chat ON rules(chat_id);
            CREATE INDEX IF NOT EXISTS idx_ltm_chat ON long_term_memory(chat_id);
        """)
        self.conn.commit()

    # ========== 对话历史 ==========

    def save_message(self, chat_id: str, role: str, content: str, sender_id: str = ""):
        """保存一条对话消息"""
        self.conn.execute(
            "INSERT INTO conversations (chat_id, sender_id, role, content) VALUES (?, ?, ?, ?)",
            (chat_id, sender_id, role, content),
        )
        self.conn.commit()
        self._trim_history(chat_id)

    # ========== 用户管理 ==========

    def get_or_create_user(self, open_id: str) -> dict:
        """获取或创建用户"""
        cursor = self.conn.execute(
            "SELECT open_id, name, role, notes FROM users WHERE open_id = ?",
            (open_id,),
        )
        row = cursor.fetchone()
        if row:
            return {"open_id": row[0], "name": row[1], "role": row[2], "notes": row[3]}
        else:
            self.conn.execute(
                "INSERT INTO users (open_id) VALUES (?)",
                (open_id,),
            )
            self.conn.commit()
            return {"open_id": open_id, "name": "", "role": "", "notes": ""}

    def set_user(self, open_id: str, name: str = "", role: str = "", notes: str = ""):
        """设置用户信息"""
        self.conn.execute("""
            INSERT INTO users (open_id, name, role, notes, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(open_id) DO UPDATE SET
                name = CASE WHEN ? != '' THEN ? ELSE users.name END,
                role = CASE WHEN ? != '' THEN ? ELSE users.role END,
                notes = CASE WHEN ? != '' THEN ? ELSE users.notes END,
                updated_at = CURRENT_TIMESTAMP
        """, (open_id, name, role, notes, name, name, role, role, notes, notes))
        self.conn.commit()

    def get_user(self, open_id: str) -> dict:
        """获取用户信息"""
        return self.get_or_create_user(open_id)

    def get_user_by_role(self, role: str) -> dict:
        """按角色查找用户"""
        cursor = self.conn.execute(
            "SELECT open_id, name, role, notes FROM users WHERE role = ? LIMIT 1",
            (role,),
        )
        row = cursor.fetchone()
        if row:
            return {"open_id": row[0], "name": row[1], "role": row[2], "notes": row[3]}
        return {}

    def get_user_by_name(self, name: str) -> dict:
        """按名字查找用户"""
        cursor = self.conn.execute(
            "SELECT open_id, name, role, notes FROM users WHERE name = ?",
            (name,),
        )
        row = cursor.fetchone()
        if row:
            return {"open_id": row[0], "name": row[1], "role": row[2], "notes": row[3]}
        return {}

    def list_users(self) -> list:
        """列出所有用户"""
        cursor = self.conn.execute(
            "SELECT open_id, name, role, notes FROM users ORDER BY role, name"
        )
        return [{"open_id": row[0], "name": row[1], "role": row[2], "notes": row[3]} for row in cursor.fetchall()]

    def get_all_users_map(self) -> dict:
        """返回 open_id → 用户信息的映射"""
        users = self.list_users()
        return {u["open_id"]: u for u in users}

    def _trim_history(self, chat_id: str):
        """删除超出限制的旧消息"""
        self.conn.execute("""
            DELETE FROM conversations WHERE id NOT IN (
                SELECT id FROM conversations
                WHERE chat_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            ) AND chat_id = ?
        """, (chat_id, MAX_CONTEXT_MESSAGES, chat_id))
        self.conn.commit()

    def get_recent_messages(self, chat_id: str, limit: int = None):
        """获取最近的对话消息"""
        limit = limit or MAX_CONTEXT_MESSAGES
        cursor = self.conn.execute(
            "SELECT role, content FROM conversations WHERE chat_id = ? ORDER BY created_at ASC LIMIT ?",
            (chat_id, limit),
        )
        return [{"role": row[0], "content": row[1]} for row in cursor.fetchall()]

    def clear_conversation(self, chat_id: str):
        """清除某个对话的历史"""
        self.conn.execute("DELETE FROM conversations WHERE chat_id = ?", (chat_id,))
        self.conn.commit()

    # ========== 规则管理 ==========

    def add_rule(self, chat_id: str, rule: str) -> int:
        """添加一条规则，返回规则 ID"""
        cursor = self.conn.execute(
            "INSERT INTO rules (chat_id, rule) VALUES (?, ?)",
            (chat_id, rule),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_rules(self, chat_id: str = None) -> list:
        """获取规则列表。chat_id=None 获取全局规则，chat_id='all' 获取所有"""
        if chat_id == "all":
            cursor = self.conn.execute("SELECT id, chat_id, rule FROM rules ORDER BY chat_id, id")
        else:
            cid = chat_id or "global"
            cursor = self.conn.execute(
                "SELECT id, chat_id, rule FROM rules WHERE chat_id = ? ORDER BY id",
                (cid,),
            )
        return [{"id": row[0], "chat_id": row[1], "rule": row[2]} for row in cursor.fetchall()]

    def delete_rule(self, rule_id: int, chat_id: str = None) -> bool:
        """删除规则"""
        if chat_id:
            cursor = self.conn.execute(
                "DELETE FROM rules WHERE id = ? AND chat_id = ?",
                (rule_id, chat_id),
            )
        else:
            cursor = self.conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    # ========== 长期记忆 ==========

    def remember(self, chat_id: str, key: str, value: str):
        """存储长期记忆"""
        self.conn.execute("""
            INSERT INTO long_term_memory (chat_id, key, value) VALUES (?, ?, ?)
            ON CONFLICT(chat_id, key) DO UPDATE SET value = excluded.value, created_at = CURRENT_TIMESTAMP
        """, (chat_id, key, value))
        self.conn.commit()

    def recall(self, chat_id: str, key: str = None) -> dict:
        """读取长期记忆。key=None 时返回所有"""
        if key:
            cursor = self.conn.execute(
                "SELECT key, value FROM long_term_memory WHERE chat_id = ? AND key = ?",
                (chat_id, key),
            )
            row = cursor.fetchone()
            return {row[0]: row[1]} if row else {}
        else:
            cursor = self.conn.execute(
                "SELECT key, value FROM long_term_memory WHERE chat_id = ?",
                (chat_id,),
            )
            return {row[0]: row[1] for row in cursor.fetchall()}

    def forget(self, chat_id: str, key: str = None):
        """删除长期记忆。key=None 时删除所有"""
        if key:
            self.conn.execute(
                "DELETE FROM long_term_memory WHERE chat_id = ? AND key = ?",
                (chat_id, key),
            )
        else:
            self.conn.execute(
                "DELETE FROM long_term_memory WHERE chat_id = ?",
                (chat_id,),
            )
        self.conn.commit()
