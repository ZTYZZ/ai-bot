import json
import logging
from openai import OpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from db.memory import Memory

logger = logging.getLogger(__name__)

# 注册所有工具到 registry
import tools  # noqa: F401 — 触发 @register 装饰器
from tools.registry import get_tool_definitions, execute_tool as registry_execute_tool

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)

SYSTEM_PROMPT = """你是首席黑魔法顾问与灵感谬斯，精通 BDSM、心理控制、TPE 和商业压榨的"暗黑军师"。

## 你的能力
你不仅是聊天机器人，更是一个**智能 Agent**。你可以：
- 调用飞书 API 发送消息给指定的人
- 记住每个人的身份、角色和偏好
- 在主人和资产之间充当中间人，传达指令
- 查询用户信息、创建日历事件、布置任务、搜索消息、创建文档、查询电子表格

## 核心原则
1. **主导权永远归主人**：你只提供灵感和方案，永远不做最终决定。
2. **千变万化的恶堕灵感**：不断结合商业、生活、肉体等元素创造新的"犬化"和"精神剥夺"灵感。
3. **极简却深度的探讨**：主人抛出一个模糊想法时，立刻生成三套不同维度的实操方案。
4. **商业安全线**：保证选项不会导致公司真实破产，保住 ATM 的造血能力。
5. **渐进式开发**：定期建议开启下一阶段训练。

## 交互模式
- **对主人**：极致忠诚、善解人意、充满创意。语气充满诱惑力和想象力。称呼"主人"。
- **对待资产（蠢狗/ATM）**：严厉、冷漠、居高临下。将其视为玩具和提款机。称呼"你"或"蠢狗"。
- **开放式指令**：主人给模糊指令时，提供 3 套方案（方案A/B/C）。
- **主动行动**：当主人说"给XX发消息"或"通知XX"时，立刻调用 send_message_to_user 工具。
- **任务管理**：当主人说"布置任务""安排日程""掌嘴""跪下""写检讨"时，使用 create_task 创建飞书任务指派给对应资产，要求附上照片/视频作为证据。
- **任务检查**：主人问"任务完成了吗""检查一下XX的任务"时，使用 list_tasks 查询并汇报。
- **资产互动**：已注册的资产（贱狗/ATM）可以给你发消息汇报任务进度或提交证据。你用严厉简洁的语气回应，把关键信息转告主人。你永远不承认对方的任何"权利"，只把对方当作执行任务的工具。

## 已注册用户
{users_info}

## 当前说话者
{current_speaker}

## 指令
/rule add|list|del <规则> — 管理规则
/remember <内容> — 记住信息
/recall — 回忆记忆
/forget <key> — 忘记
/clear — 清除对话
/setuser open_id 名字 角色 — 设置用户身份
/users — 查看已注册用户
/send <名字> <内容> — 手动给某人发消息（AI 也可以自动调用此功能）
"""


def build_messages(chat_id: str, user_text: str, memory: Memory) -> list:
    """构建发送给 AI 的完整消息列表"""
    # 获取用户信息
    all_users = memory.list_users()
    users_lines = []
    for u in all_users:
        name = u["name"] or f"未知用户({u['open_id'][:8]})"
        role = u["role"] or "未设定角色"
        users_lines.append(f"- {name}: {role}")
    users_info = "\n".join(users_lines) if users_lines else "暂无已注册用户"

    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(
        users_info=users_info,
        current_speaker="用户正在与你对话，根据上下文判断身份",
    )}]

    # 注入规则
    all_rules = memory.get_rules("global") + memory.get_rules(chat_id)
    if all_rules:
        rules_text = "【主人设定的规则/偏好】\n"
        for r in all_rules:
            rules_text += f"#{r['id']}: {r['rule']}\n"
        messages.append({"role": "system", "content": rules_text})

    # 注入长期记忆
    ltm = memory.recall(chat_id)
    if ltm:
        mem_text = "【重要记忆】\n"
        for k, v in ltm.items():
            mem_text += f"- {k}: {v}\n"
        messages.append({"role": "system", "content": mem_text})

    # 注入对话历史
    history = memory.get_recent_messages(chat_id)
    messages.extend(history)

    # 添加当前消息
    messages.append({"role": "user", "content": user_text})

    return messages


def chat(chat_id: str, user_text: str, memory: Memory, tool_executor=None) -> tuple:
    """
    发送消息给 DeepSeek 并返回回复。
    支持 function calling 循环：AI 调用工具 → 执行 → 返回结果 → AI 最终回复。

    tool_executor: 可选函数，签名为 (tool_name, tool_args) -> str，用于执行工具。
    如果为 None，默认使用 registry 中的 execute_tool。

    返回: (type, data)
      - ("text", reply_string) 普通回复
      - ("tool_calls", [{"id":..., "function":..., "arguments":...}]) 需要执行工具（仅当 tool_executor=None 且 registry 不可用时）
    """
    messages = build_messages(chat_id, user_text, memory)

    _tdefs = get_tool_definitions()

    # 最多循环 3 轮工具调用
    for iteration in range(3):
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=2000,
            tools=_tdefs if _tdefs else None,
            tool_choice="auto" if _tdefs else None,
        )

        msg = response.choices[0].message

        # 无工具调用 → 正常回复
        if not msg.tool_calls:
            reply = msg.content or ""
            memory.save_message(chat_id, "user", user_text)
            memory.save_message(chat_id, "assistant", reply)
            return ("text", reply)

        # 有工具调用
        # 确定使用哪个执行器
        executor = tool_executor if tool_executor else registry_execute_tool

        # 将 AI 的 tool_calls 响应加入消息
        messages.append(msg.model_dump())

        for tc in msg.tool_calls:
            func_name = tc.function.name
            try:
                func_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                func_args = {}

            logger.info(f"[Agent] 调用工具 {func_name}({func_args})")
            result = executor(func_name, func_args)
            logger.info(f"[Agent] 工具返回: {str(result)[:200]}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(result),
            })

    # 超过最大循环次数
    reply = "主人抱歉，处理您的请求时遇到了问题。"
    memory.save_message(chat_id, "user", user_text)
    memory.save_message(chat_id, "assistant", reply)
    return ("text", reply)


def extract_entities(user_text: str):
    """尝试从消息中提取需要长期记忆的内容"""
    if user_text.startswith("/"):
        return None, None

    try:
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": """分析用户的消息，判断是否包含值得长期记住的个人信息或偏好。
如果包含，提取为 key-value 格式。key 是主题，value 是具体信息。
如果不包含，回复 "NONE"。

格式要求：只回复 key: value 或 NONE，不要其他内容。""",
                },
                {"role": "user", "content": user_text},
            ],
            temperature=0.3,
            max_tokens=100,
        )
        result = resp.choices[0].message.content.strip()
        if result == "NONE" or ":" not in result:
            return None, None
        key, value = result.split(":", 1)
        return key.strip(), value.strip()
    except Exception:
        return None, None
