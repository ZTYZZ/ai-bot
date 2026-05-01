"""文档与表格工具 — 创建文档、查询电子表格"""
from tools.registry import register


def _get_feishu_client():
    import app
    return app.feishu_client


@register(
    name="create_doc",
    description="创建飞书文档。用于起草规则、合同、计划方案。主人说「帮我创建一个文档」「起草一份XX」时使用。",
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "文档标题，如「资产管理规则」「训练计划」",
            },
            "folder_token": {
                "type": "string",
                "description": "目标文件夹 token（可选，不填则放在根目录）",
            },
        },
        "required": ["title"],
    },
)
def create_doc(args: dict) -> str:
    title = args.get("title", "")
    if not title:
        return "请提供文档标题。"

    folder_token = args.get("folder_token", "")

    client = _get_feishu_client()
    result = client.create_doc(title=title, folder_token=folder_token)

    if result.get("code") == 0:
        url = result.get("url", "")
        return f"文档「{title}」已成功创建。链接：{url}"
    else:
        return f"创建文档失败：{result.get('msg')}"


@register(
    name="query_sheet_data",
    description="查询飞书电子表格的元数据（工作表名称、行列数）。用于查看表格结构。主人说「看看这个表格」「表格里有什么」时使用。",
    parameters={
        "type": "object",
        "properties": {
            "spreadsheet_token": {
                "type": "string",
                "description": "电子表格的 spreadsheet_token（从表格URL中获取，如 Feishu.cn/sheets/XXX 中的 XXX 部分）",
            },
        },
        "required": ["spreadsheet_token"],
    },
)
def query_sheet_data(args: dict) -> str:
    spreadsheet_token = args.get("spreadsheet_token", "")
    if not spreadsheet_token:
        return "请提供电子表格的 spreadsheet_token。"

    client = _get_feishu_client()
    result = client.query_sheet_data(spreadsheet_token=spreadsheet_token)

    if result.get("code") != 0:
        return f"查询表格失败：{result.get('msg')}"

    sheets = result.get("sheets", [])
    if not sheets:
        return "该电子表格中暂无工作表。"

    lines = ["电子表格中的工作表："]
    for s in sheets:
        title = s.get("title", "未命名")
        rows = s.get("row_count", "?")
        cols = s.get("column_count", "?")
        sheet_id = s.get("sheet_id", "")
        lines.append(f"- {title} ({rows}行 x {cols}列, id={sheet_id})")

    return "\n".join(lines)
