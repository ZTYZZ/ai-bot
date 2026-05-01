# 导入工具模块，触发 @register 装饰器注册
from tools import messaging  # send_message_to_user
from tools import tasks  # create_task, list_task（需要 task:task:readwrite 权限）

# list_known_users: 只查本地数据库，不调飞书 API，安全
from tools.registry import register as _register


@_register(
    name="list_known_users",
    description="列出所有已注册的用户及其角色信息。用于了解系统中已有哪些用户。",
    parameters={"type": "object", "properties": {}},
)
def list_known_users(args: dict) -> str:
    from tools.context import get_memory
    memory = get_memory()
    users = memory.list_users()
    if not users:
        return "暂无已注册用户。"
    lines = ["已注册用户："]
    for u in users:
        name = u["name"] or "未命名"
        role = u["role"] or "未设定角色"
        lines.append(f"- {name} ({role})")
    return "\n".join(lines)


# from tools import users  # get_user_info / list_tenant_users 需要额外权限，暂不启用
# from tools import calendar
# from tools import tasks
# from tools import search
# from tools import docs
