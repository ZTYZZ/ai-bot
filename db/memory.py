import os
from datetime import datetime

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    DateTime,
    JSON,
    func,
    UniqueConstraint,
)
from sqlalchemy.orm import sessionmaker, scoped_session, declarative_base

from config import DB_PATH

Base = declarative_base()


# ============================================================
# ORM Models (mirror the existing SQLite schema)
# ============================================================

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, nullable=False, index=True)
    sender_id = Column(String, default="")
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Rule(Base):
    __tablename__ = "rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, nullable=False, default="global", index=True)
    rule = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    open_id = Column(String, unique=True, nullable=True)   # 飞书 open_id（可为空，允许仅 QQ 绑定）
    name = Column(String, default="")
    role = Column(String, default="")
    notes = Column(Text, default="")
    qq_id = Column(String, unique=True, nullable=True)     # QQ 机器人用户 ID
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LongTermMemory(Base):
    __tablename__ = "long_term_memory"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, nullable=False, default="global", index=True)
    key = Column(String, nullable=False)
    value = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("chat_id", "key"),)


class AssetProfile(Base):
    """资产驯化档案 — 每个被驯化资产一行"""
    __tablename__ = "asset_profiles"
    id = Column(Integer, primary_key=True, autoincrement=True)
    open_id = Column(String, unique=True, nullable=False, index=True)

    # 核心评分维度 (0-100)
    obedience = Column(Integer, default=50)     # 服从度
    attitude = Column(Integer, default=50)      # 态度
    diligence = Column(Integer, default=50)     # 勤勉度
    creativity = Column(Integer, default=50)    # 创造力
    endurance = Column(Integer, default=50)     # 忍耐力

    # 统计数据
    tasks_assigned = Column(Integer, default=0)
    tasks_completed = Column(Integer, default=0)
    tasks_failed = Column(Integer, default=0)
    punishments = Column(Integer, default=0)
    rewards = Column(Integer, default=0)

    # 驯化方向
    training_focus = Column(Text, default="")
    master_notes = Column(Text, default="")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())


class AssetLog(Base):
    """资产行为档案 — 每次奖惩/评估/任务结果一条记录"""
    __tablename__ = "asset_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    open_id = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False)  # evaluation/punishment/reward/task_complete/task_fail/attitude/note
    description = Column(Text, default="")
    score_changes = Column(JSON, default=dict)   # {"obedience": +5, "attitude": -3}
    created_at = Column(DateTime, server_default=func.now())


# ============================================================
# Memory Class (public API identical to the original)
# ============================================================

class Memory:
    def __init__(self, database_url: str = None):
        if database_url is None:
            database_url = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

        self.engine = create_engine(
            database_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,  # verify connections before use (important for Supabase pooler)
        )
        self._session_factory = sessionmaker(bind=self.engine)
        self.Session = scoped_session(self._session_factory)

        # Auto-create tables on first run (idempotent)
        Base.metadata.create_all(self.engine)

        # Auto-migrate: add any missing columns to existing tables
        self._migrate()

        # Compatibility: expose a conn-like attribute for external use (e.g. /reset)
        # For raw SQL, use the engine directly.
        self.conn = self  # delegate to reset_all() / custom methods

    def _migrate(self):
        """Minimal auto-migration: add missing columns so deploys don't need manual SQL."""
        is_pg = "postgresql" in str(self.engine.url) or "postgres" in str(self.engine.url)
        with self.engine.connect() as conn:
            if is_pg:
                # qq_id column added for QQ bot integration
                conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS qq_id VARCHAR UNIQUE"
                )
                # updated_at column added for user record tracking
                conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()"
                )
                # open_id 必须允许 NULL（QQ 用户没有飞书 open_id）
                conn.exec_driver_sql(
                    "ALTER TABLE users ALTER COLUMN open_id DROP NOT NULL"
                )
            else:
                # SQLite: ALTER TABLE ADD COLUMN IF NOT EXISTS is not supported
                cols = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(users)").fetchall()]
                if "qq_id" not in cols:
                    conn.exec_driver_sql("ALTER TABLE users ADD COLUMN qq_id VARCHAR")
                if "updated_at" not in cols:
                    conn.exec_driver_sql("ALTER TABLE users ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            conn.commit()

    # ========== Conversation History ==========

    def save_message(self, chat_id: str, role: str, content: str, sender_id: str = ""):
        session = self.Session()
        try:
            session.add(Conversation(
                chat_id=chat_id,
                sender_id=sender_id,
                role=role,
                content=content,
            ))
            session.commit()
            self._trim_history(session, chat_id)
        finally:
            session.close()

    def get_recent_messages(self, chat_id: str, limit: int = None) -> list:
        if limit is None:
            from config import MAX_CONTEXT_MESSAGES
            limit = MAX_CONTEXT_MESSAGES
        session = self.Session()
        try:
            rows = (
                session.query(Conversation.role, Conversation.content)
                .filter(Conversation.chat_id == chat_id)
                .order_by(Conversation.created_at.asc())
                .limit(limit)
                .all()
            )
            return [{"role": row[0], "content": row[1]} for row in rows]
        finally:
            session.close()

    def clear_conversation(self, chat_id: str):
        session = self.Session()
        try:
            session.query(Conversation).filter(Conversation.chat_id == chat_id).delete()
            session.commit()
        finally:
            session.close()

    def _trim_history(self, session, chat_id: str):
        from config import MAX_CONTEXT_MESSAGES
        # Find IDs to keep (the most recent MAX_CONTEXT_MESSAGES)
        keep_ids = [
            row[0]
            for row in session.query(Conversation.id)
            .filter(Conversation.chat_id == chat_id)
            .order_by(Conversation.created_at.desc())
            .limit(MAX_CONTEXT_MESSAGES)
            .all()
        ]
        # Delete everything else for this chat_id
        if keep_ids:
            session.query(Conversation).filter(
                Conversation.chat_id == chat_id,
                ~Conversation.id.in_(keep_ids),
            ).delete(synchronize_session=False)
        session.commit()

    # ========== User Management ==========

    def get_or_create_user(self, open_id: str) -> dict:
        session = self.Session()
        try:
            user = session.query(User).filter(User.open_id == open_id).first()
            if user:
                return self._user_to_dict(user)
            else:
                new_user = User(open_id=open_id)
                session.add(new_user)
                session.commit()
                return self._user_to_dict(new_user)
        finally:
            session.close()

    def set_user(self, open_id: str = "", name: str = "", role: str = "", notes: str = "",
                 qq_id: str = ""):
        session = self.Session()
        try:
            user = None
            if open_id:
                user = session.query(User).filter(User.open_id == open_id).first()
            if not user and qq_id:
                user = session.query(User).filter(User.qq_id == qq_id).first()
            if user:
                if name:
                    user.name = name
                if role:
                    user.role = role
                if notes:
                    user.notes = notes
                if qq_id:
                    user.qq_id = qq_id
                if open_id:
                    user.open_id = open_id
                user.updated_at = datetime.utcnow()
            else:
                session.add(User(
                    open_id=open_id or None,
                    name=name,
                    role=role,
                    notes=notes,
                    qq_id=qq_id or None,
                ))
            session.commit()
        finally:
            session.close()

    def get_user(self, open_id: str) -> dict:
        return self.get_or_create_user(open_id)

    def get_user_by_role(self, role: str) -> dict:
        session = self.Session()
        try:
            user = session.query(User).filter(User.role == role).first()
            return self._user_to_dict(user) if user else {}
        finally:
            session.close()

    def get_user_by_name(self, name: str) -> dict:
        session = self.Session()
        try:
            user = session.query(User).filter(User.name == name).first()
            return self._user_to_dict(user) if user else {}
        finally:
            session.close()

    def get_user_by_qq_id(self, qq_id: str) -> dict:
        session = self.Session()
        try:
            user = session.query(User).filter(User.qq_id == qq_id).first()
            return self._user_to_dict(user) if user else {}
        finally:
            session.close()

    def get_or_create_user_by_qq(self, qq_id: str) -> dict:
        session = self.Session()
        try:
            user = session.query(User).filter(User.qq_id == qq_id).first()
            if user:
                return self._user_to_dict(user)
            new_user = User(qq_id=qq_id)
            session.add(new_user)
            session.commit()
            return self._user_to_dict(new_user)
        finally:
            session.close()

    def bind_qq_to_user(self, open_id: str, qq_id: str) -> bool:
        """将 QQ ID 绑定到已有的飞书用户。如果 QQ ID 已被绑定则先解绑。"""
        session = self.Session()
        try:
            # 找到目标用户
            user = session.query(User).filter(User.open_id == open_id).first()
            if not user:
                return False
            # 如果 QQ ID 已被其他人绑定，先解绑
            existing = session.query(User).filter(User.qq_id == qq_id).first()
            if existing and existing.id != user.id:
                existing.qq_id = None
            # 绑定
            user.qq_id = qq_id
            # 删除孤立 QQ 占位条目（无 open_id、无 role、同一 qq_id 的其他行）
            orphan_count = session.query(User).filter(
                User.qq_id == qq_id,
                User.open_id == None,
                User.role == "",
                User.id != user.id,
            ).delete(synchronize_session=False)
            if orphan_count:
                logger.info(f"清理了 {orphan_count} 个孤立 QQ 用户条目")
            session.commit()
            return True
        finally:
            session.close()

    def list_users(self) -> list:
        session = self.Session()
        try:
            users = (
                session.query(User)
                .order_by(User.role, User.name)
                .all()
            )
            return [self._user_to_dict(u) for u in users]
        finally:
            session.close()

    @staticmethod
    def _user_to_dict(u) -> dict:
        return {
            "open_id": u.open_id or "",
            "name": u.name or "",
            "role": u.role or "",
            "notes": u.notes or "",
            "qq_id": u.qq_id or "",
        }

    def get_all_users_map(self) -> dict:
        users = self.list_users()
        return {u["open_id"]: u for u in users}

    # ========== Rules ==========

    def add_rule(self, chat_id: str, rule: str) -> int:
        session = self.Session()
        try:
            r = Rule(chat_id=chat_id, rule=rule)
            session.add(r)
            session.commit()
            return r.id
        finally:
            session.close()

    def get_rules(self, chat_id: str = None) -> list:
        session = self.Session()
        try:
            if chat_id == "all":
                rows = session.query(Rule).order_by(Rule.chat_id, Rule.id).all()
            else:
                cid = chat_id or "global"
                rows = (
                    session.query(Rule)
                    .filter(Rule.chat_id == cid)
                    .order_by(Rule.id)
                    .all()
                )
            return [{"id": r.id, "chat_id": r.chat_id, "rule": r.rule} for r in rows]
        finally:
            session.close()

    def delete_rule(self, rule_id: int, chat_id: str = None) -> bool:
        session = self.Session()
        try:
            q = session.query(Rule).filter(Rule.id == rule_id)
            if chat_id:
                q = q.filter(Rule.chat_id == chat_id)
            count = q.delete()
            session.commit()
            return count > 0
        finally:
            session.close()

    # ========== Long-Term Memory ==========

    def remember(self, chat_id: str, key: str, value: str):
        session = self.Session()
        try:
            existing = (
                session.query(LongTermMemory)
                .filter(LongTermMemory.chat_id == chat_id, LongTermMemory.key == key)
                .first()
            )
            if existing:
                existing.value = value
                existing.created_at = datetime.utcnow()
            else:
                session.add(LongTermMemory(chat_id=chat_id, key=key, value=value))
            session.commit()
        finally:
            session.close()

    def recall(self, chat_id: str, key: str = None) -> dict:
        session = self.Session()
        try:
            if key:
                row = (
                    session.query(LongTermMemory.key, LongTermMemory.value)
                    .filter(LongTermMemory.chat_id == chat_id, LongTermMemory.key == key)
                    .first()
                )
                return {row[0]: row[1]} if row else {}
            else:
                rows = (
                    session.query(LongTermMemory.key, LongTermMemory.value)
                    .filter(LongTermMemory.chat_id == chat_id)
                    .all()
                )
                return {row[0]: row[1] for row in rows}
        finally:
            session.close()

    def forget(self, chat_id: str, key: str = None):
        session = self.Session()
        try:
            q = session.query(LongTermMemory).filter(LongTermMemory.chat_id == chat_id)
            if key:
                q = q.filter(LongTermMemory.key == key)
            q.delete()
            session.commit()
        finally:
            session.close()

    # ========== Asset Profile ==========

    def get_or_create_asset_profile(self, open_id: str) -> dict:
        session = self.Session()
        try:
            profile = session.query(AssetProfile).filter(AssetProfile.open_id == open_id).first()
            if profile:
                return self._profile_to_dict(profile)
            new_p = AssetProfile(open_id=open_id)
            session.add(new_p)
            session.commit()
            return self._profile_to_dict(new_p)
        finally:
            session.close()

    def get_asset_profile(self, open_id: str) -> dict:
        session = self.Session()
        try:
            profile = session.query(AssetProfile).filter(AssetProfile.open_id == open_id).first()
            return self._profile_to_dict(profile) if profile else {}
        finally:
            session.close()

    def update_asset_profile(self, open_id: str, **kwargs):
        session = self.Session()
        try:
            profile = session.query(AssetProfile).filter(AssetProfile.open_id == open_id).first()
            if profile:
                for key, value in kwargs.items():
                    if hasattr(profile, key):
                        setattr(profile, key, value)
                profile.updated_at = datetime.utcnow()
                session.commit()
            else:
                kwargs["open_id"] = open_id
                session.add(AssetProfile(**kwargs))
                session.commit()
        finally:
            session.close()

    def list_asset_profiles(self) -> list:
        session = self.Session()
        try:
            profiles = session.query(AssetProfile).order_by(AssetProfile.open_id).all()
            return [self._profile_to_dict(p) for p in profiles]
        finally:
            session.close()

    def add_asset_log(self, open_id: str, event_type: str, description: str = "",
                      score_changes: dict = None) -> int:
        session = self.Session()
        try:
            log = AssetLog(
                open_id=open_id,
                event_type=event_type,
                description=description,
                score_changes=score_changes or {},
            )
            session.add(log)
            session.commit()
            return log.id
        finally:
            session.close()

    def get_asset_logs(self, open_id: str, limit: int = 20) -> list:
        session = self.Session()
        try:
            logs = (
                session.query(AssetLog)
                .filter(AssetLog.open_id == open_id)
                .order_by(AssetLog.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": l.id,
                    "event_type": l.event_type,
                    "description": l.description,
                    "score_changes": l.score_changes or {},
                    "created_at": l.created_at.strftime("%Y-%m-%d %H:%M") if l.created_at else "",
                }
                for l in logs
            ]
        finally:
            session.close()

    def get_asset_full_report(self, open_id: str) -> dict:
        """返回资产的完整档案：基本分 + 最近日志 + 统计摘要"""
        profile = self.get_asset_profile(open_id)
        logs = self.get_asset_logs(open_id, limit=15)
        profile["recent_logs"] = logs
        profile["completion_rate"] = (
            round(profile.get("tasks_completed", 0) / max(profile.get("tasks_assigned", 1), 1) * 100)
        )
        return profile

    @staticmethod
    def _profile_to_dict(p) -> dict:
        return {
            "open_id": p.open_id,
            "obedience": p.obedience,
            "attitude": p.attitude,
            "diligence": p.diligence,
            "creativity": p.creativity,
            "endurance": p.endurance,
            "tasks_assigned": p.tasks_assigned,
            "tasks_completed": p.tasks_completed,
            "tasks_failed": p.tasks_failed,
            "punishments": p.punishments,
            "rewards": p.rewards,
            "training_focus": p.training_focus or "",
            "master_notes": p.master_notes or "",
        }

    # ========== Bulk Operations ==========

    def reset_all(self):
        """Clear all data (used by /reset command)."""
        session = self.Session()
        try:
            session.query(Conversation).delete()
            session.query(Rule).delete()
            session.query(User).delete()
            session.query(LongTermMemory).delete()
            session.query(AssetProfile).delete()
            session.query(AssetLog).delete()
            session.commit()
        finally:
            session.close()
