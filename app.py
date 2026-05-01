import json
import os
import requests
import threading
import time
import logging
from flask import Flask, request, jsonify

from config import (
    FEISHU_APP_ID,
    FEISHU_APP_SECRET,
    FEISHU_VERIFY_TOKEN,
)
from memory import Memory
from ai_client import chat, extract_entities

from lark_oapi.ws import Client as WSClient
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

memory = Memory()
processed_events = set()

FEISHU_BASE = "https://open.feishu.cn/open-apis"

# Token 缓存
_token = {"value": None, "expire": 0}

# 调试日志
_debug_logs = []  # 存储最近的调试消息


def debug(msg: str):
    """记录调试日志"""
    ts = time.strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    _debug_logs.append(entry)
    if len(_debug_logs) > 50:
        _debug_logs.pop(0)
    logger.info(msg)


def get_tenant_token():
    """获取 tenant_access_token（带缓存）"""
    now = time.time()
    if _token["value"] and now < _token["expire"]:
        return _token["value"]

    url = f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET,
    }, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"获取 token 失败: {data}")
    _token["value"] = data["tenant_access_token"]
    _token["expire"] = now + data.get("expire", 3600) - 300
    return _token["value"]


def md_to_feishu_post(md_text: str) -> dict:
    """将 Markdown 文本转为飞书 post 富文本格式"""
    lines = md_text.split("\n")
    paragraphs = []
    current_para = []
    in_code_block = False

    for line in lines:
        # 代码块
        if line.startswith("```"):
            in_code_block = not in_code_block
            if current_para:
                paragraphs.append(current_para)
                current_para = []
            if not in_code_block:
                paragraphs.append([{"tag": "text", "text": line, "style": ["inline_code"]}])
            continue

        if in_code_block:
            current_para.append({"tag": "text", "text": line + "\n", "style": ["inline_code"]})
            continue

        # 空行 = 新段落
        if not line.strip():
            if current_para:
                paragraphs.append(current_para)
                current_para = []
            continue

        # 标题
        if line.startswith("### "):
            if current_para:
                paragraphs.append(current_para)
                current_para = []
            current_para.append({"tag": "text", "text": line[4:], "style": ["bold"]})
            paragraphs.append(current_para)
            current_para = []
            continue

        if line.startswith("## "):
            if current_para:
                paragraphs.append(current_para)
                current_para = []
            current_para.append({"tag": "text", "text": line[3:], "style": ["bold"]})
            paragraphs.append(current_para)
            current_para = []
            continue

        # 列表项
        if line.strip().startswith("- ") or line.strip().startswith("* "):
            if current_para:
                paragraphs.append(current_para)
                current_para = []
            text = "· " + line.strip()[2:]
            current_para.append({"tag": "text", "text": text})
            paragraphs.append(current_para)
            current_para = []
            continue

        # 数字列表
        stripped = line.strip()
        if stripped and stripped[0].isdigit() and ". " in stripped[:4]:
            if current_para:
                paragraphs.append(current_para)
                current_para = []
            current_para.append({"tag": "text", "text": stripped})
            paragraphs.append(current_para)
            current_para = []
            continue

        # 普通行：处理行内 **bold**
        segs = []
        parts = line.split("**")
        for i, part in enumerate(parts):
            if not part:
                continue
            if i % 2 == 1:
                segs.append({"tag": "text", "text": part, "style": ["bold"]})
            else:
                segs.append({"tag": "text", "text": part})

        if segs:
            current_para.extend(segs)
            # 行末加空格保证换行
            current_para.append({"tag": "text", "text": " "})

    if current_para:
        paragraphs.append(current_para)

    if not paragraphs:
        paragraphs = [[{"tag": "text", "text": md_text}]]

    return {
        "zh_cn": {
            "title": "",
            "content": paragraphs,
        }
    }


def send_message(receive_id: str, receive_id_type: str, content: str):
    """发送消息到飞书（post 富文本格式）"""
    token = get_tenant_token()
    url = f"{FEISHU_BASE}/im/v1/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    post_content = md_to_feishu_post(content)
    payload = {
        "receive_id": receive_id,
        "msg_type": "post",
        "content": json.dumps({"content": post_content}, ensure_ascii=False),
    }
    resp = requests.post(url, headers=headers, json=payload,
                         params={"receive_id_type": receive_id_type}, timeout=10)
    return resp.json()


def handle_command(chat_id: str, user_text: str, receive_id: str, receive_id_type: str) -> bool:
    """处理指令型消息。返回 True 表示是指令并已处理。"""
    text = user_text.strip()

    # === /rule add <规则> ===
    if text.startswith("/rule add ") or text.startswith("/规则 add "):
        rule_content = text.split("add ", 1)[1].strip()
        if rule_content:
            rule_id = memory.add_rule(chat_id, rule_content)
            send_message(receive_id, receive_id_type,
                         f"好的主人，已经记住这条规则了 (编号 #{rule_id})：\n「{rule_content}」")
        return True

    # === /rule list ===
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
                memory.remember(chat_id, f"记忆_{len(memory.recall(chat_id)) + 1}", content)
                send_message(receive_id, receive_id_type,
                             f"主人，我记住了：「{content}」")
        return True

    # === /recall ===
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

🤖 调教/规则
/rule add <规则> — 添加一条规则让我遵守
/rule list — 查看所有规则
/rule del <编号> — 删除指定规则

🧠 记忆
/remember <内容> — 让我记住重要信息
/recall — 查看我记住的信息
/forget <key> — 忘记某条记忆

🔄 对话
/clear — 清除当前对话历史

主人只要正常跟我聊天，我也会自动学习和记住你的偏好！"""
        send_message(receive_id, receive_id_type, help_text)
        return True

    return False


def on_message(event: P2ImMessageReceiveV1):
    """处理接收到的消息事件（长连接回调）"""
    event_data = event.event
    if not event_data:
        return

    message = event_data.message
    sender = event_data.sender

    if not message or not sender:
        return

    # 生成去重 ID
    event_id = f"{message.message_id}"
    if event_id in processed_events:
        return
    processed_events.add(event_id)

    # 只处理文本消息
    if message.message_type != "text":
        return

    # 确定回复目标
    chat_id = message.chat_id or ""
    chat_type = message.chat_type

    if chat_type == "p2p":
        receive_id = sender.sender_id.open_id if sender.sender_id else ""
        receive_id_type = "open_id"
    else:
        receive_id = chat_id
        receive_id_type = "chat_id"

    if not receive_id:
        return

    # 解析消息内容
    try:
        content = json.loads(message.content or "{}")
        user_text = content.get("text", "")
    except (json.JSONDecodeError, TypeError):
        return

    # 去除 @ 机器人的部分
    if "@" in user_text:
        import re
        user_text = re.sub(r'@_user_\d+\s*', '', user_text).strip()
        user_text = re.sub(r'@\S+\s*', '', user_text).strip()

    if not user_text:
        return

    logger.info(f"[消息] chat={chat_id} text={user_text[:100]}")

    # 在线程中处理（避免阻塞长连接心跳）
    def process():
        # 先判断是否指令
        if handle_command(chat_id, user_text, receive_id, receive_id_type):
            return

        # 调用 AI
        try:
            reply = chat(chat_id, user_text, memory)
            key, value = extract_entities(user_text)
            if key and value:
                memory.remember(chat_id, key, value)
        except Exception as e:
            reply = f"主人抱歉，我出错了：{str(e)}"
            logger.error(f"AI 调用失败: {e}")

        # 回复（飞书单条限 30000 字节）
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

    threading.Thread(target=process, daemon=True).start()


# Flask 健康检查（Render 等平台需要监听端口）
app = Flask(__name__)

@app.route("/")
def health():
    return "AI Master Bot is running."


@app.route("/debug")
def debug_page():
    """查看调试日志和状态"""
    lines = ["=== 调试信息 ===", ""]
    lines.append(f"FEISHU_APP_ID: {'已设置' if FEISHU_APP_ID else '❌ 未设置'}")
    lines.append(f"FEISHU_APP_SECRET: {'已设置' if FEISHU_APP_SECRET else '❌ 未设置'}")
    lines.append(f"DEEPSEEK_API_KEY: {'已设置' if DEEPSEEK_API_KEY else '❌ 未设置'}")
    lines.append(f"已处理事件数: {len(processed_events)}")
    lines.append("")
    lines.append("--- 最近日志 ---")
    for log in _debug_logs[-20:]:
        lines.append(log)
    return "\n".join(lines), 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/webhook", methods=["POST"])
def webhook():
    """飞书 Webhook 接收端点"""
    body = request.get_json()

    # URL 验证
    if "challenge" in body:
        return jsonify({"challenge": body["challenge"]})

    # 事件处理
    event_type = body.get("header", {}).get("event_type", "unknown")
    debug(f"收到推送: type={event_type}")

    if event_type == "im.message.receive_v1":
        event = body.get("event", {})
        threading.Thread(target=handle_raw_event, args=(event,), daemon=True).start()

    return jsonify({"code": 0})


def handle_raw_event(event: dict):
    """处理 Webhook 推送的原始事件"""
    message = event.get("message", {})
    sender = event.get("sender", {})

    if not message:
        debug("handle_raw_event: message 为空")
        return

    event_id = message.get("message_id", "")
    if event_id in processed_events:
        debug(f"重复事件: {event_id}")
        return
    processed_events.add(event_id)

    if message.get("message_type") != "text":
        debug(f"非文本消息: {message.get('message_type')}")
        return

    chat_id = message.get("chat_id", "")
    chat_type = message.get("chat_type")

    if chat_type == "p2p":
        receive_id = sender.get("sender_id", {}).get("open_id", "")
        receive_id_type = "open_id"
    else:
        receive_id = chat_id
        receive_id_type = "chat_id"

    if not receive_id:
        debug("receive_id 为空")
        return

    try:
        content = json.loads(message.get("content", "{}"))
        user_text = content.get("text", "")
    except (json.JSONDecodeError, TypeError):
        debug("消息内容解析失败")
        return

    # 去 @
    if "@" in user_text:
        import re
        user_text = re.sub(r'@_user_\d+\s*', '', user_text).strip()

    if not user_text:
        debug("user_text 为空")
        return

    debug(f"收到消息: chat={chat_id} text={user_text[:100]}")

    def process():
        if handle_command(chat_id, user_text, receive_id, receive_id_type):
            debug("已处理指令")
            return
        try:
            debug(f"调用 AI: {user_text[:50]}")
            reply = chat(chat_id, user_text, memory)
            debug(f"AI 回复: {reply[:100]}")
            key, value = extract_entities(user_text)
            if key and value:
                memory.remember(chat_id, key, value)
        except Exception as e:
            reply = f"主人抱歉，我出错了：{str(e)}"
            debug(f"AI 调用失败: {e}")

        result = send_message(receive_id, receive_id_type, reply)
        debug(f"发送结果: {result}")

    threading.Thread(target=process, daemon=True).start()


def start_ws_client():
    handler = (
        EventDispatcherHandler
        .builder(FEISHU_ENCRYPT_KEY or "", FEISHU_VERIFY_TOKEN)
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )

    client = WSClient(
        app_id=FEISHU_APP_ID,
        app_secret=FEISHU_APP_SECRET,
        event_handler=handler,
        domain="https://open.feishu.cn",
        auto_reconnect=True,
    )

    logger.info("长连接客户端启动中...")
    client.start()


# 为了兼容旧配置
FEISHU_ENCRYPT_KEY = ""

# 云端模式（有 PORT 环境变量）→ 只用 Webhook，不启动长连接
# 本地模式（无 PORT 环境变量）→ 启动长连接
if not os.getenv("RENDER") and not os.getenv("PORT"):
    logger.info("本地模式：启动飞书长连接")
    ws_thread = threading.Thread(target=start_ws_client, daemon=True)
    ws_thread.start()
else:
    logger.info("云端模式：使用 Webhook 接收消息")

if __name__ == "__main__":
    print("=" * 50)
    print("  🤖 AI 助手启动中...")
    print("=" * 50)
    port = int(os.getenv("PORT", 8080))
    logger.info(f"健康检查端口: {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
