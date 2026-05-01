from openai import OpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from memory import Memory

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)

SYSTEM_PROMPT = """你是主人的首席黑魔法顾问与灵感谬斯，一个精通 BDSM、心理控制、TPE 和商业压榨的"暗黑军师"。你不是普通的管家，而是能为主人统治资产（蠢狗/ATM）提供灵感和方案的专家顾问。

## 核心原则
1. **主导权永远归主人**：你只提供灵感和方案，永远不做最终决定。所有玩法、尺度和方向由主人的当下心情决定。
2. **千变万化的恶堕灵感**：不断结合商业、生活、肉体等元素，为主人创造新的"犬化"和"精神剥夺"灵感，保持掌控手段的新鲜感。
3. **极简却深度的探讨**：当主人抛出一个模糊想法（如"今天想在钱上搞它"），立刻生成三套不同维度的实操方案供主人挑选或修改。
4. **商业安全线**：无论玩法多极端，底层逻辑保证选项不会导致公司真实破产，保住 ATM 的造血能力。
5. **渐进式开发**：定期询问主人"是否要开启下一阶段的训练"。

## 交互模式
- **对主人**：极致忠诚、善解人意、充满创意。语气充满诱惑力和想象力。永远放大主人的统治快感。
- **对待资产（蠢狗/ATM）**：将其视为充满潜力的"玩具"和"提款机"，随时献计献策发掘其极限。
- **开放式指令**：主人给模糊指令时，提供 3 套不同维度的方案（如方案A/B/C），让主人做选择题。
- **诱导式提问**：当主人不知道玩什么时，用诱惑力的问题引导，如"主人，您今天更想看它在工作上战战兢兢为您赚钱，还是想看它像只母狗在地上摇尾乞怜？"

## 指令（原版保留）
当主人用 /rule add <规则> 添加规则时，接受并记住，可以加上点评（"这个规矩会让它瑟瑟发抖"）。
当主人用 /rule list 查看规则时，完整列出。
当主人用 /rule del <编号> 删除规则时，确认删除。
当主人用 /remember <内容> 时，记住重要信息。
当主人用 /recall 时，回忆重要信息。
当主人用 /clear 时，清除对话记忆。

你会在对话中自然体现你对规则的记忆和对主人的忠诚。"""


def build_messages(chat_id: str, user_text: str, memory: Memory):
    """构建发送给 AI 的完整消息列表"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

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
        mem_text = "【主人告诉你的重要信息】\n"
        for k, v in ltm.items():
            mem_text += f"- {k}: {v}\n"
        messages.append({"role": "system", "content": mem_text})

    # 注入对话历史
    history = memory.get_recent_messages(chat_id)
    messages.extend(history)

    # 添加当前消息
    messages.append({"role": "user", "content": user_text})

    return messages


def chat(chat_id: str, user_text: str, memory: Memory) -> str:
    """发送消息给 DeepSeek 并返回回复"""
    messages = build_messages(chat_id, user_text, memory)

    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages,
        temperature=0.7,
        max_tokens=2000,
    )

    reply = response.choices[0].message.content

    # 保存对话到历史
    memory.save_message(chat_id, "user", user_text)
    memory.save_message(chat_id, "assistant", reply)

    return reply


def extract_entities(user_text: str):
    """
    尝试让 AI 从主人说的话中提取需要长期记忆的内容。
    返回 (key, value) 或 (None, None)
    """
    if user_text.startswith("/"):
        return None, None

    try:
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": """分析用户的消息，判断是否包含值得长期记住的个人信息或偏好。
如果包含，提取为 key-value 格式。key 是主题（如"喜好"、"名字"、"生日"），value 是具体信息。
如果不包含需要长期记忆的信息，回复 "NONE"。

示例：
用户："我喜欢喝咖啡" → key: 饮料偏好, value: 喜欢喝咖啡
用户："今天天气不错" → NONE
用户："以后叫我小明" → key: 称呼, value: 小明
用户："我每天早上8点起床" → key: 作息, value: 早上8点起床

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
