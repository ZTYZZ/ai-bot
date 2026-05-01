import json
import requests
import threading
import urllib.parse
from flask import Flask, request, jsonify

from config import (
    FEISHU_APP_ID,
    FEISHU_APP_SECRET,
    FEISHU_VERIFY_TOKEN,
)
from memory import Memory
from ai_client import chat, extract_entities

app = Flask(__name__)
memory = Memory()
processed_events = set()

# 飞书 API 基础
FEISHU_BASE = "https://open.feishu.cn/open-apis"


def get_tenant_token():
    """获取 tenant_access_token"""
    url = f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET,
    }, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"获取 token 失败: {data}")
    return data["tenant_access_token"]


def get_message_content(message_id: str, token: str) -> str:
    """获取消息的原始内容（处理 @ 提及）"""
    url = f"{FEISHU_BASE}/im/v1/messages/{message_id}"
    resp = requests.get(url, headers={
        "Authorization": f"Bearer {token}",
    }, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        return ""

    items = data.get("data", {}).get("items", [])
    for item in items:
        body = item.get("body", {})
        content = body.get("content", "")
        # 解析 JSON 内容
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                text = parsed.get("text", "")
                # 去掉 @ 提及
                if isinstance(text, str) and "@" in text:
                    # 简单处理：提取纯文本
                    text = text.strip()
                return text
        except (json.JSONDecodeError, TypeError):
            return content
    return ""


def send_message(receive_id: str, receive_id_type: str, content: str):
    """发送消息到飞书"""
    token = get_tenant_token()
    url = f"{FEISHU_BASE}/im/v1/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": content}, ensure_ascii=False),
    }
    resp = requests.post(url, headers=headers, json=payload,
                         params={"receive_id_type": receive_id_type}, timeout=10)
    return resp.json()


def send_card(receive_id: str, receive_id_type: str, title: str, content: str):
    """发送卡片消息"""
    token = get_tenant_token()
    url = f"{FEISHU_BASE}/im/v1/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    card = {
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "blue",
        },
        "elements": [
            {"tag": "markdown", "content": content}
        ],
    }
    payload = {
        "receive_id": receive_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    resp = requests.post(url, headers=headers, json=payload,
                         params={"receive_id_type": receive_id_type}, timeout=10)
    return resp.json()


def handle_command(chat_id: str, user_text: str, receive_id: str, receive_id_type: str) -> bool:
    """
    处理指令型消息。返回 True 表示是指令并已处理。
    """
    text = user_text.strip()

    # === /rule add <规则> ===
    if text.startswith("/rule add ") or text.startswith("/规则 add "):
        rule_content = text.split("add ", 1)[1].strip()
        if rule_content:
            rule_id = memory.add_rule(chat_id, rule_content)
            send_message(receive_id, receive_id_type,
                         f"好的主人，已经记住这条规则了 (编号 #{rule_id})：\n「{rule_content}」")
        return True

    # === /rule list / 规则列表 ===
    if text in ["/rule list", "/规则 list", "/rule", "/规则"]:
        all_rules = memory.get_rules("global") + memory.get_rules(chat_id)
        if not all_rules:
            send_message(receive_id, receive_id_type,
                         "主人，目前还没有设定任何规则哦。用 /rule add <规则内容> 来添加吧。")
        else:
            lines = ["主人，这是您当前设定的规则："]
            for r in all_rules:
                scope = "全局" if r["chat_id"] == "global" else "当前会话"
                lines.append(f"#{r['id']} [{scope}]: {r['rule']}")
            send_message(receive_id, receive_id_type, "\n".join(lines))
        return True

    # === /rule del <id> ===
    if text.startswith("/rule del ") or text.startswith("/规则 del "):
        try:
            rule_id = int(text.split("del ", 1)[1].strip())
            if memory.delete_rule(rule_id, chat_id):
                send_message(receive_id, receive_id_type, f"主人，规则 #{rule_id} 已删除。")
            else:
                send_message(receive_id, receive_id_type, f"主人，没找到规则 #{rule_id}。")
        except ValueError:
            send_message(receive_id, receive_id_type, "主人，格式是：/rule del <编号>")
        return True

    # === /remember <内容> ===
    if text.startswith("/remember ") or text.startswith("/记 "):
        content = text.split(" ", 1)[1].strip()
        if content:
            key, value = extract_entities(content)
            if key and value:
                memory.remember(chat_id, key, value)
                send_message(receive_id, receive_id_type,
                             f"已记住主人：{key} = {value}")
            else:
                # 单纯存储
                memory.remember(chat_id, f"记忆_{len(memory.recall(chat_id)) + 1}", content)
                send_message(receive_id, receive_id_type,
                             f"主人，我记住了：「{content}」")
        return True

    # === /recall / 回忆 ===
    if text in ["/recall", "/回忆"]:
        mems = memory.recall(chat_id)
        if not mems:
            send_message(receive_id, receive_id_type, "主人，我目前没有关于您的特别记忆。")
        else:
            lines = ["主人，我记得这些："]
            for k, v in mems.items():
                lines.append(f"- {k}: {v}")
            send_message(receive_id, receive_id_type, "\n".join(lines))
        return True

    # === /forget <key> ===
    if text.startswith("/forget ") or text.startswith("/忘 "):
        key = text.split(" ", 1)[1].strip()
        memory.forget(chat_id, key)
        send_message(receive_id, receive_id_type, f"主人，我忘记了「{key}」。")
        return True

    # === /clear ===
    if text in ["/clear", "/清除"]:
        memory.clear_conversation(chat_id)
        send_message(receive_id, receive_id_type, "主人，当前对话记忆已清除。")
        return True

    # === /help ===
    if text in ["/help", "/帮助", "/?"]:
        help_text = """主人，这些是您可以对我使用的指令：

**🤖 调教/规则**
/rule add <规则> — 添加一条规则让我遵守
/rule list — 查看所有规则
/rule del <编号> — 删除指定规则

**🧠 记忆**
/remember <内容> — 让我记住重要信息
/recall — 查看我记住的信息
/forget <key> — 忘记某条记忆

**🔄 对话**
/clear — 清除当前对话历史

主人只要正常跟我聊天，我也会自动学习和记住你的偏好！"""
        send_message(receive_id, receive_id_type, help_text)
        return True

    return False


def handle_event(event: dict):
    """处理飞书事件"""
    event_id = event.get("sender", {}).get("id", "") + "_" + (event.get("message", {}).get("message_id", ""))
    if event_id in processed_events:
        return
    processed_events.add(event_id)

    message = event.get("message", {})

    # 只处理文本消息
    if message.get("message_type") != "text":
        return

    # 确定回复目标
    chat_type = message.get("chat_type")  # "p2p" 私聊 或 "group" 群聊
    chat_id = message.get("chat_id", "")

    if chat_type == "p2p":
        sender = event.get("sender", {})
        receive_id = sender.get("sender_id", {}).get("open_id")
        receive_id_type = "open_id"
    else:
        receive_id = chat_id
        receive_id_type = "chat_id"

    # 获取消息内容
    content = message.get("content", "{}")
    try:
        parsed = json.loads(content)
        user_text = parsed.get("text", "")
    except (json.JSONDecodeError, TypeError):
        return

    if not user_text.strip():
        return

    print(f"[消息] chat={chat_id} text={user_text[:100]}")

    # 先判断是否是指令
    if handle_command(chat_id, user_text, receive_id, receive_id_type):
        return

    # 非指令：调用 AI，同时自动提取长期记忆
    try:
        reply = chat(chat_id, user_text, memory)

        # 自动提取并存储长期记忆
        key, value = extract_entities(user_text)
        if key and value:
            memory.remember(chat_id, key, value)

    except Exception as e:
        reply = f"主人抱歉，我出错了：{str(e)}"
        print(f"[错误] {e}")

    # 回复消息（飞书单条限 30000 字节，太长分段）
    if len(reply.encode("utf-8")) > 28000:
        chunks = []
        current = ""
        for line in reply.split("\n"):
            if len((current + line).encode("utf-8")) > 28000:
                chunks.append(current)
                current = line + "\n"
            else:
                current += line + "\n"
        if current:
            chunks.append(current)
        for chunk in chunks:
            send_message(receive_id, receive_id_type, chunk)
    else:
        send_message(receive_id, receive_id_type, reply)


# ========== Flask 路由 ==========

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json()

    # URL 验证
    if "challenge" in body:
        return jsonify({"challenge": body["challenge"]})

    # 事件处理
    event_type = body.get("header", {}).get("event_type", "")
    if event_type == "im.message.receive_v1":
        event = body.get("event", {})
        threading.Thread(target=handle_event, args=(event,), daemon=True).start()

    return jsonify({"code": 0})


@app.route("/", methods=["GET"])
def index():
    return "AI Master Bot is running."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
