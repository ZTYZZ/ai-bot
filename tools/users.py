"""用户相关工具 — 查看已注册用户、查询飞书用户信息、列出租户用户"""
import json
from tools.registry import register


def _get_memory():
    import app
    return app.memory


def _get_feishu_client():
    import app
    return app.feishu_client


@register(
    name="list_known_users",
    description="列出所有已注册的用户及其角色信息。用于了解系统中已有哪些用户。",
    parameters={"type": "object", "properties": {}},
)
def list_known_users(args: dict) -> str:
    memory = _get_memory()
    users = memory.list_users()
    if not users:
        return "暂无已注册用户。让需要管理的人给机器人发一条消息，然后用 /setuser 命令注册。"
    lines = ["已注册用户："]
    for u in users:
        name = u["name"] or "未命名"
        role = u["role"] or "未设定角色"
        lines.append(f"- {name} ({role})")
    return "\n".join(lines)


@register(
    name="get_user_info",
    description="查询指定飞书用户的详细信息，包括姓名、部门、职位、邮箱、手机号等。当主人想了解某人时使用。",
    parameters={
        "type": "object",
        "properties": {
            "user_name": {
                "type": "string",
                "description": "要查询的用户名字，如「刘神」「张三」",
            },
        },
        "required": ["user_name"],
    },
)
def get_user_info(args: dict) -> str:
    user_name = args.get("user_name", "")
    if not user_name:
        return "请指定要查询的用户名字。"

    memory = _get_memory()
    user = memory.get_user_by_name(user_name)

    if not user:
        return f"找不到已注册用户「{user_name}」。请先用 /setuser 注册该用户。"

    client = _get_feishu_client()
    result = client.get_user_info(user["open_id"])

    if result.get("code") != 0:
        return f"查询失败：{result.get('msg')}"

    info = result.get("user")
    if not info:
        return f"未找到用户 {user_name} 的信息。"

    lines = [f"用户信息：{info['name']}"]
    if info.get("job_title"):
        lines.append(f"职位：{info['job_title']}")
    if info.get("email"):
        lines.append(f"邮箱：{info['email']}")
    if info.get("mobile"):
        lines.append(f"手机：{info['mobile']}")
    if info.get("employee_no"):
        lines.append(f"工号：{info['employee_no']}")
    return "\n".join(lines)


@register(
    name="list_tenant_users",
    description="列出飞书租户中的所有用户。用于发现和了解组织中有哪些人。返回用户名、部门、职位等信息。",
    parameters={
        "type": "object",
        "properties": {
            "page_size": {
                "type": "integer",
                "description": "每页返回的用户数量，默认50",
            },
        },
        "required": [],
    },
)
def list_tenant_users(args: dict) -> str:
    page_size = args.get("page_size", 50)

    client = _get_feishu_client()
    result = client.list_tenant_users(page_size=page_size)

    if result.get("code") != 0:
        return f"列出租户用户失败：{result.get('msg')}"

    users = result.get("users", [])
    if not users:
        return "租户下暂无用户。"

    lines = [f"租户用户列表（共 {len(users)} 人）："]
    for u in users[:30]:  # 限制输出前30条
        name = u.get("name", "未命名")
        job = u.get("job_title", "")
        dept = f" - {job}" if job else ""
        lines.append(f"- {name}{dept}")

    if len(users) > 30:
        lines.append(f"... 还有 {len(users) - 30} 人未显示")

    if result.get("has_more"):
        lines.append("（还有更多用户，可增加 page_size 查看）")

    return "\n".join(lines)
