"""日历工具 — 创建和查看日历事件"""
from datetime import datetime
from tools.registry import register


def _get_memory():
    import app
    return app.memory


def _get_feishu_client():
    import app
    return app.feishu_client


@register(
    name="create_calendar_event",
    description="创建飞书日历事件。用于安排会议、设定截止日期、安排仪式性活动。主人说「帮我在日历上加一个事件」「安排一个会议」时使用。",
    parameters={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "事件标题，如「周报截止」「每日跪拜仪式」",
            },
            "start_time": {
                "type": "string",
                "description": "开始时间，ISO 8601格式，如「2026-04-28T14:00:00」",
            },
            "end_time": {
                "type": "string",
                "description": "结束时间，ISO 8601格式，如「2026-04-28T15:00:00」",
            },
            "description": {
                "type": "string",
                "description": "事件描述（可选）",
            },
        },
        "required": ["summary", "start_time", "end_time"],
    },
)
def create_calendar_event(args: dict) -> str:
    summary = args.get("summary", "")
    start_time = args.get("start_time", "")
    end_time = args.get("end_time", "")
    description = args.get("description", "")

    if not summary or not start_time or not end_time:
        return "创建日历事件需要提供 summary、start_time 和 end_time 参数。"

    client = _get_feishu_client()
    result = client.create_calendar_event(
        summary=summary,
        start_time=start_time,
        end_time=end_time,
        description=description,
    )

    if result.get("code") == 0:
        return f"日历事件「{summary}」已成功创建。"
    else:
        return f"创建日历事件失败：{result.get('msg')}"


@register(
    name="list_calendar_events",
    description="查询日历事件列表。用于查看某段时间的日程安排。主人问「最近有什么安排」「看看下周的日程」时使用。",
    parameters={
        "type": "object",
        "properties": {
            "start_time": {
                "type": "string",
                "description": "查询起始日期，格式如「2026-04-01」（可选）",
            },
            "end_time": {
                "type": "string",
                "description": "查询结束日期，格式如「2026-04-30」（可选）",
            },
        },
        "required": [],
    },
)
def list_calendar_events(args: dict) -> str:
    start_time = args.get("start_time", "")
    end_time = args.get("end_time", "")

    client = _get_feishu_client()
    result = client.list_calendar_events(
        start_time=start_time,
        end_time=end_time,
    )

    if result.get("code") != 0:
        return f"查询日历事件失败：{result.get('msg')}"

    events = result.get("events", [])
    if not events:
        return "该时间段内暂无日历事件。"

    lines = ["日历事件："]
    for e in events[:20]:
        summary = e.get("summary", "无标题")
        st = e.get("start_time", {})
        if isinstance(st, dict):
            st_str = st.get("timestamp", st.get("date", "?"))
        else:
            st_str = str(st)
        lines.append(f"- {summary} (开始: {st_str})")

    if len(events) > 20:
        lines.append(f"... 还有 {len(events) - 20} 个事件")

    return "\n".join(lines)
