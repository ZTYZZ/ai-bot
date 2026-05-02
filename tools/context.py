"""共享上下文 — 避免循环导入。app.py 在启动时注入依赖。

⚠️ 使用 threading.local() 避免多线程竞态条件：
每个线程（每个请求）有自己独立的 sender 上下文，不会互相覆盖。
"""
import threading

_memory = None
_feishu_client = None
_qq_client = None
_cron_mode = False            # 巡航模式标记，绕过权限检查

# 每个线程独立的请求上下文（解决多线程竞态覆盖问题）
_local = threading.local()


def set_memory(m):
    global _memory
    _memory = m


def set_feishu_client(c):
    global _feishu_client
    _feishu_client = c


def set_qq_client(c):
    global _qq_client
    _qq_client = c


def set_current_sender(sender_id: str, qq_id: str = None):
    _local.sender_id = sender_id
    _local.qq_sender_id = qq_id


def get_current_sender() -> str:
    sid = getattr(_local, "sender_id", "") or ""
    qid = getattr(_local, "qq_sender_id", "") or ""
    return sid or qid


def set_cron_mode(enabled: bool):
    global _cron_mode
    _cron_mode = enabled


def get_memory():
    return _memory


def get_feishu_client():
    return _feishu_client


def get_qq_client():
    return _qq_client


def is_master() -> bool:
    """检查当前发送者是否为主人（工具权限校验用）。

    返回 True 的条件（满足任一即可）：
    1. 当前发送者的 role == "主人"（通过 open_id 或 qq_id 查找）
    2. 处于巡航模式（cron agent 调用）
    3. 系统中还没有注册主人（初始化场景）
    """
    if _cron_mode:
        return True
    if _memory is None:
        return True

    sender_id = getattr(_local, "sender_id", "") or ""
    qq_sender_id = getattr(_local, "qq_sender_id", "") or ""

    user = {}
    if sender_id:
        user = _memory.get_user(sender_id)
    if not user.get("role") and qq_sender_id:
        user = _memory.get_user_by_qq_id(qq_sender_id)

    role = user.get("role", "")
    if role == "主人":
        return True

    # 如果系统中根本没有主人，当前发送者就是事实上的主人
    master = _memory.get_user_by_role("主人")
    if not master:
        return True

    return False


def require_master() -> str | None:
    """如果当前发送者不是主人，返回拒绝消息；否则返回 None"""
    if is_master():
        return None
    # 获取当前发送者信息用于日志
    if _memory:
        sender_id = getattr(_local, "sender_id", "") or ""
        if sender_id:
            user = _memory.get_user(sender_id)
            name = user.get("name") or "未知"
            role = user.get("role") or "未注册"
            return f"⛔ 权限拒绝：「{name}」（{role}）无权执行此操作。只有主人可以。"
    return "⛔ 权限拒绝：无法验证身份。"
