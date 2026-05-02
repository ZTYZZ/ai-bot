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
    from tools.context import get_memory, require_master
    reject = require_master()
    if reject:
        return reject
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
    from tools.context import get_memory, require_master
    reject = require_master()
    if reject:
        return reject
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
    from tools.context import get_memory, require_master
    reject = require_master()
    if reject:
        return reject
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
    from tools.context import get_memory, require_master
    reject = require_master()
    if reject:
        return reject
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
    from tools.context import get_memory, require_master
    reject = require_master()
    if reject:
        return reject
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


@_register(
    name="get_asset_report",
    description="查看指定资产的完整驯化档案，包括各项评分、统计数据、最近行为日志和完成率。主人说「看看XX的情况」「XX最近表现如何」「评价一下XX」时使用。",
    parameters={
        "type": "object",
        "properties": {
            "user_name": {
                "type": "string",
                "description": "资产的名字",
            },
        },
        "required": ["user_name"],
    },
)
def get_asset_report(args: dict) -> str:
    from tools.context import get_memory, require_master
    reject = require_master()
    if reject:
        return reject
    memory = get_memory()
    user_name = args.get("user_name", "")
    if not user_name:
        return "请指定资产名字。"
    user = memory.get_user_by_name(user_name)
    if not user:
        return f"找不到用户「{user_name}」。"
    report = memory.get_asset_full_report(user["open_id"])
    if not report or "open_id" not in report:
        return f"「{user_name}」暂无驯化档案。已自动创建，下次评估时会填充数据。"

    lines = [f"📋 {user_name} 的驯化档案\n"]
    lines.append("【评分维度】")
    lines.append(f"  服从度：{report.get('obedience', 50)}/100")
    lines.append(f"  态度：  {report.get('attitude', 50)}/100")
    lines.append(f"  勤勉度：{report.get('diligence', 50)}/100")
    lines.append(f"  创造力：{report.get('creativity', 50)}/100")
    lines.append(f"  忍耐力：{report.get('endurance', 50)}/100")
    lines.append("")
    lines.append("【统计数据】")
    lines.append(f"  任务：分配 {report.get('tasks_assigned', 0)} / 完成 {report.get('tasks_completed', 0)} / 失败 {report.get('tasks_failed', 0)}")
    lines.append(f"  完成率：{report.get('completion_rate', 0)}%")
    lines.append(f"  惩罚：{report.get('punishments', 0)} 次")
    lines.append(f"  奖励：{report.get('rewards', 0)} 次")
    if report.get("training_focus"):
        lines.append(f"\n【驯化重点】{report['training_focus']}")
    if report.get("master_notes"):
        lines.append(f"【主人备注】{report['master_notes']}")

    logs = report.get("recent_logs", [])
    if logs:
        lines.append("\n【最近行为记录】")
        for log in logs[:10]:
            sc = log.get("score_changes", {})
            sc_str = ", ".join(f"{k}: {v:+d}" for k, v in sc.items()) if sc else "无变化"
            lines.append(f"  [{log.get('event_type', '?')}] {log.get('description', '')} ({sc_str}) — {log.get('created_at', '')}")

    return "\n".join(lines)


@_register(
    name="evaluate_asset",
    description="给资产的某个维度打分或调整分数。主人说「XX服从度+10」「XX态度太差了扣分」或者与资产互动后AI自动评估时使用。",
    parameters={
        "type": "object",
        "properties": {
            "user_name": {
                "type": "string",
                "description": "资产的名字",
            },
            "category": {
                "type": "string",
                "description": "评分维度：obedience(服从度)/attitude(态度)/diligence(勤勉度)/creativity(创造力)/endurance(忍耐力)",
            },
            "score_change": {
                "type": "integer",
                "description": "分数变化，正数加分负数扣分，如 +5 或 -10",
            },
            "reason": {
                "type": "string",
                "description": "评分原因，如「按时完成任务」「顶嘴态度恶劣」",
            },
        },
        "required": ["user_name", "category", "score_change", "reason"],
    },
)
def evaluate_asset(args: dict) -> str:
    from tools.context import get_memory
    memory = get_memory()
    user_name = args.get("user_name", "") or args.get("asset_name", "")
    category = args.get("category", "")
    score_change = args.get("score_change", 0)
    reason = args.get("reason", "") or args.get("description", "")

    valid_categories = {"obedience", "attitude", "diligence", "creativity", "endurance"}
    if category not in valid_categories:
        return f"无效的评分维度「{category}」。可选：{', '.join(valid_categories)}"

    user = memory.get_user_by_name(user_name)
    if not user:
        return f"找不到用户「{user_name}」。"

    profile = memory.get_or_create_asset_profile(user["open_id"])
    current = profile.get(category, 50)
    new_val = max(0, min(100, current + score_change))

    memory.update_asset_profile(user["open_id"], **{category: new_val})
    memory.add_asset_log(
        user["open_id"],
        event_type="evaluation",
        description=reason,
        score_changes={category: score_change},
    )

    arrow = "↑" if score_change > 0 else "↓" if score_change < 0 else "→"
    return f"「{user_name}」{category}：{current} {arrow} {new_val}（{'+' if score_change > 0 else ''}{score_change}）。原因：{reason}"


@_register(
    name="record_asset_event",
    description="记录资产的奖惩事件或行为。主人说「XX应该受罚」「奖励XX」或者AI与资产互动后自动记录时使用。",
    parameters={
        "type": "object",
        "properties": {
            "user_name": {
                "type": "string",
                "description": "资产的名字",
            },
            "event_type": {
                "type": "string",
                "description": "事件类型：punishment(惩罚)/reward(奖励)/task_complete(任务完成)/task_fail(任务失败)/attitude(态度问题)/note(备注)",
            },
            "description": {
                "type": "string",
                "description": "事件描述",
            },
            "score_changes": {
                "type": "object",
                "description": "分数变更，如 {\"obedience\": -5, \"attitude\": -3}。无变化则传 {}。",
            },
        },
        "required": ["user_name", "event_type", "description"],
    },
)
def record_asset_event(args: dict) -> str:
    from tools.context import get_memory
    memory = get_memory()
    user_name = args.get("user_name", "") or args.get("asset_name", "")
    event_type = args.get("event_type", "") or args.get("event", "")
    description = args.get("description", "") or args.get("detail", "")
    score_changes = args.get("score_changes", {}) or args.get("score_change", {}) or {}

    # 如果 score_changes 传的是数字（如 0），转成空 dict
    if isinstance(score_changes, (int, float)):
        score_changes = {}

    user = memory.get_user_by_name(user_name)
    if not user:
        return f"找不到用户「{user_name}」。"

    # Update profile stats
    profile = memory.get_or_create_asset_profile(user["open_id"])
    updates = {}
    if event_type == "punishment":
        updates["punishments"] = profile.get("punishments", 0) + 1
    elif event_type == "reward":
        updates["rewards"] = profile.get("rewards", 0) + 1
    elif event_type == "task_complete":
        updates["tasks_completed"] = profile.get("tasks_completed", 0) + 1
    elif event_type == "task_fail":
        updates["tasks_failed"] = profile.get("tasks_failed", 0) + 1

    # Apply score changes
    for cat, change in (score_changes or {}).items():
        current = profile.get(cat, 50)
        updates[cat] = max(0, min(100, current + change))

    if updates:
        memory.update_asset_profile(user["open_id"], **updates)

    memory.add_asset_log(user["open_id"], event_type, description, score_changes or {})
    return f"已记录「{user_name}」的{event_type}事件：{description}"


@_register(
    name="compare_assets",
    description="对比所有资产的驯化数据。主人说「对比一下」「看看谁表现最好」「资产排名」时使用。",
    parameters={"type": "object", "properties": {}},
)
def compare_assets(args: dict) -> str:
    from tools.context import get_memory, require_master
    reject = require_master()
    if reject:
        return reject
    memory = get_memory()
    profiles = memory.list_asset_profiles()
    users_map = memory.get_all_users_map()

    if not profiles:
        return "暂无资产数据，请先设定资产角色。"

    lines = ["📊 资产对比表\n"]
    lines.append(f"{'名字':<8} {'服从':>4} {'态度':>4} {'勤勉':>4} {'创造':>4} {'忍耐':>4} {'完成率':>6} {'奖惩':>6}")
    lines.append("-" * 50)

    for p in profiles:
        uid = p["open_id"]
        user = users_map.get(uid, {})
        name = user.get("name", uid[:8]) if user else uid[:8]
        rate = p.get("completion_rate", 0) if "completion_rate" in p else (
            round(p.get("tasks_completed", 0) / max(p.get("tasks_assigned", 1), 1) * 100)
        )
        pr = f"{p.get('punishments', 0)}/{p.get('rewards', 0)}"
        lines.append(
            f"{name:<8} "
            f"{p.get('obedience', 50):>4} "
            f"{p.get('attitude', 50):>4} "
            f"{p.get('diligence', 50):>4} "
            f"{p.get('creativity', 50):>4} "
            f"{p.get('endurance', 50):>4} "
            f"{rate:>5}% "
            f"{pr:>6}"
        )

    return "\n".join(lines)


@_register(
    name="set_training_focus",
    description="设定资产的驯化重点。主人说「重点训练XX的服从」「接下来好好调教XX的忍耐力」时使用。",
    parameters={
        "type": "object",
        "properties": {
            "user_name": {
                "type": "string",
                "description": "资产的名字",
            },
            "focus": {
                "type": "string",
                "description": "驯化重点描述，如「加强服从训练」「提高忍耐力」",
            },
        },
        "required": ["user_name", "focus"],
    },
)
def set_training_focus(args: dict) -> str:
    from tools.context import get_memory, require_master
    reject = require_master()
    if reject:
        return reject
    memory = get_memory()
    user_name = args.get("user_name", "")
    focus = args.get("focus", "")

    user = memory.get_user_by_name(user_name)
    if not user:
        return f"找不到用户「{user_name}」。"

    memory.get_or_create_asset_profile(user["open_id"])
    memory.update_asset_profile(user["open_id"], training_focus=focus)
    return f"已将「{user_name}」的驯化重点设为：{focus}"

@_register(
    name="bind_qq_id",
    description="将 QQ 用户 ID 绑定到已有用户。主人说「绑定QQ：xxxx」「把这个QQ号和XX关联」时使用。",
    parameters={
        "type": "object",
        "properties": {
            "qq_id": {
                "type": "string",
                "description": "QQ 用户的 ID（可通过 QQ 端发送「我的ID」获取）",
            },
            "user_name": {
                "type": "string",
                "description": "要绑定的已有用户名（飞书端已注册的用户）",
            },
        },
        "required": ["qq_id", "user_name"],
    },
)
def bind_qq_id(args: dict) -> str:
    from tools.context import get_memory, require_master
    reject = require_master()
    if reject:
        return reject
    memory = get_memory()
    qq_id = args.get("qq_id", "")
    user_name = args.get("user_name", "")
    if not qq_id or not user_name:
        return "需要提供 qq_id 和 user_name。"
    user = memory.get_user_by_name(user_name)
    if not user:
        return f"找不到用户「{user_name}」。"
    ok = memory.bind_qq_to_user(user["open_id"], qq_id)
    if ok:
        return f"已将 QQ ID {qq_id} 绑定到「{user_name}」。现在飞书和 QQ 共享同一个身份。"
    else:
        return f"绑定失败：用户「{user_name}」可能没有 open_id。"

# from tools import users  # get_user_info / list_tenant_users 需要额外权限，暂不启用
# from tools import calendar
# from tools import tasks
# from tools import search
# from tools import docs
