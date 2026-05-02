"""共享上下文 — 避免循环导入。app.py 在启动时注入依赖。"""
_memory = None
_feishu_client = None
_current_sender_id = None  # 当前对话的发送者 open_id，用于权限校验


def set_memory(m):
    global _memory
    _memory = m


def set_feishu_client(c):
    global _feishu_client
    _feishu_client = c


def set_current_sender(sender_id: str):
    global _current_sender_id
    _current_sender_id = sender_id


def get_current_sender() -> str:
    return _current_sender_id


def get_memory():
    return _memory


def get_feishu_client():
    return _feishu_client


def is_master() -> bool:
    """检查当前发送者是否为主人（工具权限校验用）"""
    if _memory is None or _current_sender_id is None:
        return True  # 无上下文时放行（如 cron 巡航）
    user = _memory.get_user(_current_sender_id)
    return user.get("role") == "主人"


def require_master() -> str | None:
    """如果当前发送者不是主人，返回拒绝消息；否则返回 None"""
    if is_master():
        return None
    # 获取当前发送者信息用于日志
    if _memory and _current_sender_id:
        user = _memory.get_user(_current_sender_id)
        name = user.get("name") or "未知"
        role = user.get("role") or "未注册"
        return f"⛔ 权限拒绝：「{name}」（{role}）无权执行此操作。只有主人可以。"
    return "⛔ 权限拒绝：无法验证身份。"
