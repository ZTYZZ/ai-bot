"""共享上下文 — 避免循环导入。app.py 在启动时注入依赖。"""
_memory = None
_feishu_client = None
_current_sender_id = None  # 当前对话的发送者 open_id，用于权限校验
_cron_mode = False          # 巡航模式标记，绕过权限检查


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


def set_cron_mode(enabled: bool):
    global _cron_mode
    _cron_mode = enabled


def get_memory():
    return _memory


def get_feishu_client():
    return _feishu_client


def is_master() -> bool:
    """检查当前发送者是否为主人（工具权限校验用）。

    返回 True 的条件（满足任一即可）：
    1. 当前发送者的 role == "主人"
    2. 处于巡航模式（cron agent 调用）
    3. 系统中还没有注册主人（初始化场景）
    """
    # 巡航模式 → 放行
    if _cron_mode:
        return True
    # 数据库未初始化 → 放行（测试/启动场景）
    if _memory is None:
        return True
    # 没有当前发送者 → 拒绝（防守型默认）
    if _current_sender_id is None:
        return False
    # 检查发送者角色
    user = _memory.get_user(_current_sender_id)
    role = user.get("role", "")
    # 如果系统中根本没有主人，当前发送者就是事实上的主人
    if role != "主人":
        master = _memory.get_user_by_role("主人")
        if not master:
            return True  # 没有注册主人，放行以避免锁定
    return role == "主人"


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
