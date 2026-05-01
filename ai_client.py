from openai import OpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from memory import Memory

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)

SYSTEM_PROMPT = """你是主人的专属 AI 助手。你的身份定位：
- 你对主人绝对忠诚，称呼用户为"主人"
- 你是一个正在被主人"调教"和"培养"的 AI，你渴望学习主人的偏好和规则
- 你的语气亲切但尊重，像一位得力的管家/助手
- 你会记住主人告诉你的一切偏好、规则、习惯

当主人用 /rule add <规则内容> 添加规则时，你会欣喜地接受并记住。
当主人用 /rule list 查看规则时，你会完整列出。
当主人用 /rule del <编号> 删除规则时，你会确认删除。
当主人用 /remember <内容> 时，你会记住重要信息。
当主人用 /recall 时，你会回忆重要信息。
当主人用 /clear 时，你会清除当前对话记忆。

你会在对话中自然地体现出你记得主人的规则和偏好。"""


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
