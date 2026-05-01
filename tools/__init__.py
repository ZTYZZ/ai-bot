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


@_register(
    name="save_rule",
    description="保存主人设定的规则或行为偏好。当主人纠正你的行为、设定要求、或表达偏好时，自动调用此工具保存为永久规则。例如主人说「以后别啰嗦」「语气再狠一点」时使用。",
    parameters={
        "type": "object",
        "properties": {
            "rule": {
                "type": "string",
                "description": "规则内容，如「回复要简洁，不超过3句话」「对蠢狗的语气要更严厉」",
            },
        },
        "required": ["rule"],
    },
)
def save_rule(args: dict) -> str:
    from tools.context import get_memory
    memory = get_memory()
    rule = args.get("rule", "")
    if not rule:
        return "规则内容不能为空。"
    rid = memory.add_rule("global", rule)
    return f"规则已保存（ID: {rid}）：{rule}"


@_register(
    name="list_rules",
    description="列出所有已保存的规则。当主人问「有哪些规矩」「看看规则」时使用。",
    parameters={"type": "object", "properties": {}},
)
def list_rules(args: dict) -> str:
    from tools.context import get_memory
    memory = get_memory()
    rules = memory.get_rules("all")
    if not rules:
        return "暂无已保存的规则。"
    lines = [f"#{r['id']}: {r['rule']}" for r in rules]
    return "已保存的规则：\n" + "\n".join(lines)


@_register(
    name="delete_rule",
    description="删除一条规则。主人说「删掉第X条规则」「取消那个规矩」时使用。",
    parameters={
        "type": "object",
        "properties": {
            "rule_id": {
                "type": "integer",
                "description": "要删除的规则 ID，从 list_rules 返回的结果中获取",
            },
        },
        "required": ["rule_id"],
    },
)
def delete_rule(args: dict) -> str:
    from tools.context import get_memory
    memory = get_memory()
    rule_id = args.get("rule_id")
    if not rule_id:
        return "请指定要删除的规则 ID。"
    ok = memory.delete_rule(rule_id)
    return f"规则 #{rule_id} 已删除。" if ok else f"删除失败：未找到规则 #{rule_id}。"


@_register(
    name="remember_info",
    description="保存一条长期记忆。主人说「记住...」「帮我记一下...」时使用。",
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "记忆的主题/键，如「主人的生日」「蠢狗的弱点」",
            },
            "value": {
                "type": "string",
                "description": "要记住的内容",
            },
        },
        "required": ["key", "value"],
    },
)
def remember_info(args: dict) -> str:
    from tools.context import get_memory
    memory = get_memory()
    key = args.get("key", "")
    value = args.get("value", "")
    if not key or not value:
        return "需要提供 key 和 value。"
    memory.remember("global", key, value)
    return f"已记住：「{key}」→ {value}"


@_register(
    name="forget_info",
    description="忘记一条记忆。主人说「忘了关于XX的事」「别记那个了」时使用。",
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "要忘记的记忆主题/键",
            },
        },
        "required": ["key"],
    },
)
def forget_info(args: dict) -> str:
    from tools.context import get_memory
    memory = get_memory()
    key = args.get("key", "")
    if not key:
        return "请指定要忘记的内容。"
    memory.forget("global", key)
    return f"已忘记「{key}」相关的记忆。"


@_register(
    name="set_user_role",
    description="设置用户的角色。主人说「把XX设为贱狗」「XX是ATM」时自动调用。",
    parameters={
        "type": "object",
        "properties": {
            "user_name": {
                "type": "string",
                "description": "已注册用户的名字",
            },
            "role": {
                "type": "string",
                "description": "角色名称，如「贱狗」「ATM」「主人」",
            },
        },
        "required": ["user_name", "role"],
    },
)
def set_user_role(args: dict) -> str:
    from tools.context import get_memory
    memory = get_memory()
    user_name = args.get("user_name", "")
    role = args.get("role", "")
    if not user_name or not role:
        return "需要提供 user_name 和 role。"
    user = memory.get_user_by_name(user_name)
    if not user:
        return f"找不到用户「{user_name}」。请先用 /setuser 或让我知道他的 open_id。"
    memory.set_user(user["open_id"], role=role)
    return f"已将 {user_name} 的角色设为「{role}」。"

# from tools import users  # get_user_info / list_tenant_users 需要额外权限，暂不启用
# from tools import calendar
# from tools import tasks
# from tools import search
# from tools import docs
