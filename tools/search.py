"""搜索工具 — 搜索飞书消息历史"""
from tools.registry import register
from tools.context import get_feishu_client


@register(
    name="search_messages",
    description="搜索飞书消息历史。用于查找过去的讨论、承诺或信息。主人问「帮我找一下XX相关的消息」「搜索XX说过什么」时使用。",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词",
            },
            "user_name": {
                "type": "string",
                "description": "按发送者名字筛选（可选）",
            },
        },
        "required": ["query"],
    },
)
def search_messages(args: dict) -> str:
    query = args.get("query", "")
    if not query:
        return "请提供搜索关键词。"

    client = get_feishu_client()
    result = client.search_messages(query=query)

    if result.get("code") != 0:
        return f"搜索消息失败：{result.get('msg')}"

    items = result.get("items", [])
    if not items:
        return f"未找到与「{query}」相关的消息。"

    lines = [f"搜索「{query}」结果："]
    for item in items[:15]:
        sender = item.get("sender_name", "未知")
        content = item.get("content", "")
        if len(content) > 100:
            content = content[:100] + "..."
        chat = item.get("chat_name", "")
        where = f" [{chat}]" if chat else ""
        lines.append(f"- {sender}{where}: {content}")

    if len(items) > 15:
        lines.append(f"... 还有 {len(items) - 15} 条结果")

    return "\n".join(lines)
