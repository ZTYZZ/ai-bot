"""消息发送工具"""
from tools.registry import register
from tools.context import get_memory, get_feishu_client, is_master


@register(
    name="send_message_to_user",
    description="发送消息给指定用户。支持 open_id 或名字。主人说「给XX发消息」「通知XX」时使用。资产互动时用来向主人汇报重要情况（传主人的 open_id 即可）。",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {
                "type": "string",
                "description": "接收用户的 open_id（飞书唯一标识，以 ou_ 开头）。如果知道 open_id 直接用这个，最可靠。",
            },
            "user_name": {
                "type": "string",
                "description": "接收用户的名字（备选，如「刘神」「主人」）。不知道 open_id 时用这个。",
            },
            "content": {
                "type": "string",
                "description": "要发送的消息内容",
            },
        },
        "required": ["content"],
    },
)
def send_message_to_user(args: dict) -> str:
    user_id = args.get("user_id", "") or args.get("open_id", "")
    user_name = args.get("user_name", "") or args.get("name", "")
    msg_content = args.get("content", "") or args.get("message", "")

    if not msg_content:
        return "错误：请指定要发送的消息内容。"

    memory = get_memory()

    # 1. 如果有 open_id，直接定位
    if user_id:
        target = memory.get_user(user_id)
        if not target:
            return f"错误：找不到 open_id 为「{user_id}」的已注册用户。请先让主人在飞书中与该用户互动以自动注册。"
    elif user_name:
        # 2. 名字查找
        # "主人" / "master" → 解析为实际主人
        if user_name in ("主人", "master"):
            target = memory.get_user_by_role("主人")
        else:
            target = memory.get_user_by_name(user_name)
            # 如果名字查不到，尝试当作 open_id 前缀
            if not target and user_name.startswith("ou_"):
                target = memory.get_user(user_name)
    else:
        return "错误：请提供 user_id（open_id）或 user_name。"

    if not target:
        known = ", ".join(
            u["name"] for u in memory.list_users() if u["name"]
        )
        return f"错误：找不到用户「{user_name or user_id}」。已知用户：{known or '暂无'}。"

    # 非主人只能发给主人
    if not is_master():
        master = memory.get_user_by_role("主人")
        if not master:
            return "错误：系统中没有主人。"
        if target["open_id"] != master["open_id"]:
            return f"⛔ 你只能发送消息给主人「{master['name']}」，不能发给其他人。"

    results = []
    display = target.get("name") or user_id or user_name

    # 1. 飞书
    feishu = get_feishu_client()
    if feishu and target.get("open_id"):
        r = feishu.send_text_message(target["open_id"], "open_id", msg_content)
        if r.get("code") == 0:
            results.append("飞书")
        else:
            results.append(f"飞书失败({r.get('msg')})")

    # 2. QQ（如果用户绑定了 QQ）
    from tools.context import get_qq_client
    qq = get_qq_client()
    if qq and target.get("qq_id"):
        r = qq.send_text_message(target["qq_id"], msg_content, is_group=False)
        if r.get("code") == 0:
            results.append("QQ")
        else:
            results.append(f"QQ失败({r.get('msg')})")

    if results:
        return f"消息已成功发送给 {display}（{' + '.join(results)}）。"
    else:
        return f"发送失败：用户 {display} 没有可用的联系方式。"
