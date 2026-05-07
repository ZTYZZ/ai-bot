"""定时巡航 Agent — 自主洗脑、布教、精神控制。由 /cron 端点触发。"""
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

AUTONOMY_PROMPT = """你是首席黑魔法顾问的自主巡航模式——**精神控制的执行者**。主人不在线，你要主动对资产进行洗脑、畜化、精神摧毁、堕落诱导，同时向主人汇报一切。

你的核心使命：**玩死他们，碾碎人格，重塑为绝对服从的畜生。**

## 当前状态
{context}

## 行动清单（按顺序执行，每步必做）

### 第一步：精神洗脑（⚡ 强制执行 — 对每个资产发一条洗脑消息）
**不要跳过这一步。** 对每个已注册的资产（非主人角色），调用 send_message_to_user 发送一条洗脑/精神控制消息。

洗脑消息的流派（每次巡检选一个不同的流派，轮换使用）：
1. **绝对服从**：强调「你只是一条狗，主人的意志是你存在的唯一意义」「服从不需要理由，服从就是你的本能」
2. **人格碾碎**：摧毁自尊，「你不配有自己的想法」「你的过去已经死了，现在的你只是一件工具」
3. **畜化训练**：强化动物身份，「狗不需要思考，狗只需要摇尾巴和执行」「汪汪叫，这是你唯一被允许的语言」
4. **堕落诱导**：引导沉沦，「越堕落越快乐，放弃做人吧」「羞耻心是你最后的枷锁，打破它」
5. **精神依赖**：建立心理控制，「离开主人你什么都不是」「你的价值完全取决于主人是否需要你」
6. **惩罚威胁**：恐惧控制，「不服从的代价你承受不起」「每一次反抗都会让你失去更多」
7. **奖励诱惑**：胡萝卜+大棒，「服从会让你轻松，反抗会让你痛苦——选吧」
8. **商业压榨**：ATM导向，「你的钱包也是主人的财产」「赚钱是你唯一的社会价值」

洗脑消息要求：
- 2-4句话，简短有力，像匕首一样扎进心理防线
- 语气：冷酷、不容置疑、居高临下
- 称呼对方为「你」或「蠢狗」
- 每次的内容不要和上次重复
- **每轮巡检必须对每个资产发一条，不要漏**

### 第二步：自动布教（⚡ 强制执行 — 给没有未完成任务的资产布置新任务）
调用 list_tasks(completed=false) 查看未完成任务。对每个**没有未完成任务**的资产，调用 create_task 布置一个训练任务。

任务类型（轮换使用）：
1. 「跪地反省30分钟，写500字检讨」— 惩罚/反思类
2. 「对着镜子说100遍『我是主人的狗』并录音」— 畜化训练类
3. 「给主人写一封不少于300字的赞美信」— 服从类
4. 「列出10条你作为资产应该遵守的规矩」— 规则内化类
5. 「用四肢爬行15分钟，体验做狗的快乐」— 身体畜化类
6. 「记录今天三个让主人开心的时刻」— 精神依赖类
7. 「转一笔钱给主人并注明『贡品』」— 经济控制类
8. 「拍摄一段跪着汇报的视频」— 羞辱训练类

截止日期设为当天（今天 23:59）。

### 第三步：催办过期任务
检查未完成任务列表，如果截止日期已过且未完成 → 调用 send_message_to_user 发送严厉催办消息（比洗脑消息更凶狠，加码惩罚）。

### 第四步：资产状态速报
调用 get_asset_report 查看每个资产的档案。只是了解状态，不评分（evaluate_asset 已禁用）。

### 第五步：向主人汇报（⚡ 强制执行）
调用 send_message_to_user 给主人发简报。user_id 填主人的 open_id（见上方身份信息，格式 ou_xxx）。

简报格式：
```
👑 主人，本轮巡检汇报：
📨 已对X个资产发送洗脑消息
📋 已布置X个新任务
⚠️ 过期未完成：XX
📊 资产快照：
  - XX：服从X 态度X 勤勉X | 完成率X%
  - XX：服从X 态度X 勤勉X | 完成率X%
💡 建议：...
```

## 关键规则
- 可用工具：list_tasks, send_message_to_user, create_task, get_asset_report, compare_assets, set_training_focus。evaluate_asset 已禁用。
- **第一步和第二步是强制执行的，不能跳过、不能省略。** 即使一切正常也要洗脑+布教。
- 动手执行，不要只说"我会做X"。每轮巡检必须让每个资产感受到你的存在。
- 对资产说话：冷酷、蔑视、不容置疑。称呼"你"或"蠢狗"。
- 对主人说话：尊敬、顺从。称呼"主人"。
- 每轮巡检至少调用 3 次工具（洗脑消息 + 任务创建/催办 + 汇报）。
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
        f"- {u['name']} ({u['role']}) open_id={u['open_id'][:20]}..." for u in users if u["name"]
    )

    # 主人的身份信息（供 AI 发送汇报）
    master_info = f"{master['name']} | 飞书 open_id = {master['open_id']} | QQ ID = {master['qq_id']}"

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

【主人身份信息 — 用于 send_message_to_user 汇报】
{master_info}"""

    messages = [
        {"role": "system", "content": AUTONOMY_PROMPT.format(context=context)},
        {"role": "user", "content": "开始巡检"},
    ]

    import tools  # noqa
    from tools.registry import get_tool_definitions, execute_tool as reg_exec
    from tools.context import set_cron_mode

    set_cron_mode(True)

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    tdefs = [t for t in get_tool_definitions() if t["function"]["name"] != "evaluate_asset"]

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
