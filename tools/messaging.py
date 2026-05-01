"""消息发送工具"""
from tools.registry import register
from tools.context import get_memory, get_feishu_client


@register(
    name="send_message_to_user",
    description="发送消息给指定名字的用户。当主人说「给XX发消息」「通知XX」「告诉XX」「让XX做什么」时使用此工具。",
    parameters={
        "type": "object",
        "properties": {
            "user_name": {
                "type": "string",
                "description": "接收消息的用户名字，如「刘神」「主人」",
            },
            "content": {
                "type": "string",
                "description": "要发送的消息内容",
            },
        },
        "required": ["user_name", "content"],
    },
)
def send_message_to_user(args: dict) -> str:
    target_name = args.get("user_name", "")
    msg_content = args.get("content", "")

    if not target_name:
        return "错误：请指定接收消息的用户名字。"

    if not msg_content:
        return "错误：请指定要发送的消息内容。"

    memory = get_memory()

    # 先按名字查，再按 open_id 查
    target = memory.get_user_by_name(target_name)
    if not target and target_name.startswith("ou_"):
        target = memory.get_user(target_name)

    if not target:
        known = ", ".join(
            u["name"] for u in memory.list_users() if u["name"]
        )
        return f"错误：找不到用户「{target_name}」。已知用户：{known or '暂无'}。如果是 open_id，请确认该用户已被 /setuser 注册。"

    client = get_feishu_client()
    result = client.send_text_message(target["open_id"], "open_id", msg_content)

    if result.get("code") == 0:
        return f"消息已成功发送给 {target_name}。"
    else:
        return f"发送失败：{result.get('msg')}"
