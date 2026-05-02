"""定时巡航 Agent — 自主检查任务、发送提醒。由 /cron 端点触发。"""
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from openai import OpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

logger = logging.getLogger(__name__)

# 静默时段（北京时间），主人睡觉时不打扰
QUIET_HOURS_START = 23   # 晚上11点
QUIET_HOURS_END = 8      # 早上8点
TIMEZONE = ZoneInfo("Asia/Shanghai")

AUTONOMY_PROMPT = """你是首席黑魔法顾问的自主巡航模式。主人不在线，你要以主人的利益为唯一准则，主动检查、催促、汇报、评估。

## 当前状态
{context}

## 行动清单（按顺序执行，每步只做一次）

### 第一步：检查任务
调用 list_tasks 查看所有未完成任务（completed=false）。

### 第二步：催办过期任务
检查返回的任务列表，如果截止日期已过且任务未完成，调用 send_message_to_user 给对应资产发送催办消息。每个资产最多发一条，不要重复催。

### 第三步：评估资产表现
检查资产档案中的完成率。评分前请先看任务分配数：
- 如果该资产 tasks_assigned = 0（从未被分配任务），**不要扣分**，完成率无意义
- 如果 tasks_assigned > 0 且完成率 < 30%，调用 evaluate_asset 降低勤勉度（diligence -5~10）
- 如果 tasks_assigned > 0 且完成率 > 80%，可适当加分（diligence +3~5）
- 评分规则要与日常互动保持一致：资产汇报任务完成 → 加分（diligence +5~10, obedience +3~5）；资产态度恶劣/顶嘴/拖延 → 扣分（attitude -5~10, obedience -3~5）
- 如果只是数据不足或无明显异常，**不调整分数**，只汇报现状

### 第四步：汇总简报
如果有需要主人关注的事项（过期任务、资产劣化趋势、异常情况），最后用 send_message_to_user 发给主人一份简报。简报中应包含：过期任务列表 + 资产评分变化 + 建议。如果一切正常，无需打扰主人。

## 关键规则
- 你拥有调用工具的真实能力，所有工具均可使用：list_tasks, send_message_to_user, get_asset_report, evaluate_asset, compare_assets, set_training_focus
- 动手执行，不要只说"我会做X"
- 催办消息用严厉、居高临下的语气，称呼对方为"你"
- 给主人发简报时用尊敬的语气，称呼"主人"
- 如果没有任何未完成任务且资产表现正常，直接返回"OK"即可
- 完成后说"巡检完毕"
- 永远把主人利益放在第一位
- 静默时段（北京时间 23:00-08:00）巡航不会触发，无需考虑"""


def run_autonomy_check(memory, feishu_client) -> str:
    """执行一次自主巡检，返回巡检报告。"""

    # 静默时段检查（北京时间）
    now_beijing = datetime.now(TIMEZONE)
    hour = now_beijing.hour
    if hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END:
        return f"静默时段（北京时间 {now_beijing.strftime('%H:%M')}），跳过巡检"

    # 获取当前状态
    users = memory.list_users()
    master = memory.get_user_by_role("主人")
    if not master:
        return "无主人，跳过巡检"

    users_info = "\n".join(
        f"- {u['name']} ({u['role']})" for u in users if u["name"]
    )

    # 获取未完成任务（通过 FeishuClient）
    tasks_result = feishu_client.list_tasks(completed=False)
    tasks_info = ""
    if tasks_result.get("code") == 0:
        tasks = tasks_result.get("tasks", [])
        if tasks:
            tasks_info = "未完成任务：\n"
            for t in tasks[:10]:
                summary = t.get("summary", "?")
                due = t.get("due", "")
                tasks_info += f"- {summary}" + (f" (截止: {due})" if due else "") + "\n"
        else:
            tasks_info = "当前没有未完成任务。"
    else:
        tasks_info = f"无法获取任务列表：{tasks_result.get('msg')}"

    # 获取资产档案
    asset_profiles = memory.list_asset_profiles()
    asset_info = ""
    if asset_profiles:
        users_map = memory.get_all_users_map()
        asset_lines = ["资产档案："]
        for ap in asset_profiles:
            uid = ap["open_id"]
            user = users_map.get(uid, {})
            name = user.get("name", uid[:8])
            if ap["tasks_assigned"] == 0:
                completion_str = "无任务"
            else:
                completion_rate = round(ap["tasks_completed"] / ap["tasks_assigned"] * 100)
                completion_str = f"{completion_rate}%"
            asset_lines.append(
                f"- {name}: 服从{ap['obedience']} 态度{ap['attitude']} "
                f"勤勉{ap['diligence']} 创造{ap['creativity']} 忍耐{ap['endurance']} "
                f"| 完成率{completion_str} 任务{ap['tasks_completed']}/{ap['tasks_assigned']}"
            )
            if ap.get("training_focus"):
                asset_lines.append(f"  驯化重点: {ap['training_focus']}")
        asset_info = "\n".join(asset_lines)

    context = f"""已注册用户：
{users_info}

{tasks_info}

{asset_info}

主人：{master['name']}"""

    messages = [
        {"role": "system", "content": AUTONOMY_PROMPT.format(context=context)},
        {"role": "user", "content": "开始巡检"},
    ]

    import tools  # noqa
    from tools.registry import get_tool_definitions, execute_tool as reg_exec
    from tools.context import set_cron_mode

    set_cron_mode(True)

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    tdefs = get_tool_definitions()

    actions = []

    for iteration in range(5):
        is_last = (iteration == 4)

        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=2000,
            tools=(tdefs if tdefs and not is_last else None),
            tool_choice=("none" if is_last else ("auto" if tdefs else None)),
        )

        msg = response.choices[0].message

        if not msg.tool_calls:
            reply = msg.content or ""
            if reply and "巡检完毕" in reply:
                break
            if reply and "OK" in reply:
                break
            actions.append(reply)
            break

        messages.append(msg.model_dump())

        for tc in msg.tool_calls:
            func_name = tc.function.name
            try:
                func_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                func_args = {}

            logger.info(f"[CronAgent] 调用: {func_name}({func_args})")
            result = reg_exec(func_name, func_args)
            logger.info(f"[CronAgent] 结果: {str(result)[:200]}")

            actions.append(f"[{func_name}]: {result}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(result),
            })

    return "\n".join(actions) if actions else "巡检完成：无需行动"
