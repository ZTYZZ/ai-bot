"""任务工具 — 创建和查看飞书任务"""
from tools.registry import register


def _get_memory():
    import app
    return app.memory


def _get_feishu_client():
    import app
    return app.feishu_client


@register(
    name="create_task",
    description="创建飞书任务并指派给指定用户。用于布置任务、设定要求、跟踪完成情况。主人说「给XX布置一个任务」「让XX做XX」时使用。",
    parameters={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "任务标题，如「完成周报」「跪着写检讨」",
            },
            "description": {
                "type": "string",
                "description": "任务详细描述（可选）",
            },
            "assignee_name": {
                "type": "string",
                "description": "指派给谁（已注册用户的名字）。不填则只创建任务不分配。",
            },
            "due_date": {
                "type": "string",
                "description": "截止日期，格式如「2026-04-28」或「2026-04-28T18:00:00」（可选）",
            },
        },
        "required": ["summary"],
    },
)
def create_task(args: dict) -> str:
    summary = args.get("summary", "")
    description = args.get("description", "")
    assignee_name = args.get("assignee_name", "")
    due_date = args.get("due_date", "")

    if not summary:
        return "创建任务需要提供 summary（任务标题）。"

    member_open_ids = []
    if assignee_name:
        memory = _get_memory()
        user = memory.get_user_by_name(assignee_name)
        if not user:
            known = ", ".join(
                u["name"] for u in memory.list_users() if u["name"]
            )
            return f"找不到用户「{assignee_name}」。已知用户：{known or '暂无'}。请先用 /setuser 注册。"
        member_open_ids.append(user["open_id"])

    client = _get_feishu_client()
    result = client.create_task(
        summary=summary,
        description=description,
        due_date=due_date,
        member_open_ids=member_open_ids,
    )

    if result.get("code") == 0:
        assignee_info = f"，已指派给 {assignee_name}" if assignee_name else ""
        due_info = f"，截止：{due_date}" if due_date else ""
        return f"任务「{summary}」已成功创建{assignee_info}{due_info}。"
    else:
        return f"创建任务失败：{result.get('msg')}"


@register(
    name="list_tasks",
    description="查询飞书任务列表。用于检查任务完成情况。主人问「看看有哪些任务」「XX任务完成了吗」时使用。",
    parameters={
        "type": "object",
        "properties": {
            "completed": {
                "type": "boolean",
                "description": "按完成状态筛选：true=已完成，false=未完成。不填则返回全部。",
            },
        },
        "required": [],
    },
)
def list_tasks(args: dict) -> str:
    completed = args.get("completed", None)

    client = _get_feishu_client()
    result = client.list_tasks(completed=completed)

    if result.get("code") != 0:
        return f"查询任务失败：{result.get('msg')}"

    tasks = result.get("tasks", [])
    if not tasks:
        status = "已完成" if completed else "未完成" if completed is False else ""
        return f"暂无{status}任务。" if status else "暂无任务。"

    lines = ["任务列表："]
    for t in tasks[:20]:
        summary = t.get("summary", "无标题")
        status_icon = "✅" if t.get("completed") else "⬜"
        due = t.get("due", {})
        due_str = ""
        if isinstance(due, dict) and due.get("timestamp"):
            due_str = f" (截止: {due['timestamp']})"
        elif due:
            due_str = f" (截止: {due})"
        lines.append(f"  {status_icon} {summary}{due_str}")

    if len(tasks) > 20:
        lines.append(f"  ... 还有 {len(tasks) - 20} 个任务")

    if result.get("has_more"):
        lines.append("（还有更多任务未显示）")

    return "\n".join(lines)
