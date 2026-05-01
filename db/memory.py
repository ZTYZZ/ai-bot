import os
from datetime import datetime

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    DateTime,
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
    open_id = Column(String, unique=True, nullable=False)
    name = Column(String, default="")
    role = Column(String, default="")
    notes = Column(Text, default="")
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

        # Compatibility: expose a conn-like attribute for external use (e.g. /reset)
        # For raw SQL, use the engine directly.
        self.conn = self  # delegate to reset_all() / custom methods

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
                return {
                    "open_id": user.open_id,
                    "name": user.name,
                    "role": user.role,
                    "notes": user.notes or "",
                }
            else:
                new_user = User(open_id=open_id)
                session.add(new_user)
                session.commit()
                return {
                    "open_id": new_user.open_id,
                    "name": new_user.name,
                    "role": new_user.role,
                    "notes": "",
                }
        finally:
            session.close()

    def set_user(self, open_id: str, name: str = "", role: str = "", notes: str = ""):
        session = self.Session()
        try:
            user = session.query(User).filter(User.open_id == open_id).first()
            if user:
                if name:
                    user.name = name
                if role:
                    user.role = role
                if notes:
                    user.notes = notes
                user.updated_at = datetime.utcnow()
            else:
                session.add(User(
                    open_id=open_id,
                    name=name,
                    role=role,
                    notes=notes,
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
            if user:
                return {
                    "open_id": user.open_id,
                    "name": user.name,
                    "role": user.role,
                    "notes": user.notes or "",
                }
            return {}
        finally:
            session.close()

    def get_user_by_name(self, name: str) -> dict:
        session = self.Session()
        try:
            user = session.query(User).filter(User.name == name).first()
            if user:
                return {
                    "open_id": user.open_id,
                    "name": user.name,
                    "role": user.role,
                    "notes": user.notes or "",
                }
            return {}
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
            return [
                {
                    "open_id": u.open_id,
                    "name": u.name,
                    "role": u.role,
                    "notes": u.notes or "",
                }
                for u in users
            ]
        finally:
            session.close()

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

    # ========== Bulk Operations ==========

    def reset_all(self):
        """Clear all data (used by /reset command)."""
        session = self.Session()
        try:
            session.query(Conversation).delete()
            session.query(Rule).delete()
            session.query(User).delete()
            session.query(LongTermMemory).delete()
            session.commit()
        finally:
            session.close()
