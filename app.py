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
    DEEPSEEK_API_KEY,
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
    """发送消息到飞书"""
    token = get_tenant_token()
    url = f"{FEISHU_BASE}/im/v1/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    # 纯文本模式，兼容性最好
    payload = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": content}, ensure_ascii=False),
    }
    resp = requests.post(url, headers=headers, json=payload,
                         params={"receive_id_type": receive_id_type}, timeout=10)
    result = resp.json()
    # 文本发送失败时尝试简化内容
    if result.get("code") != 0:
        debug(f"发送失败({result.get('code')}): {result.get('msg')}")
        # 截断过长内容重试
        if len(content) > 5000:
            short = content[:5000] + "\n...(内容过长已截断)"
            payload["content"] = json.dumps({"text": short}, ensure_ascii=False)
            resp = requests.post(url, headers=headers, json=payload,
                                 params={"receive_id_type": receive_id_type}, timeout=10)
            result = resp.json()
    return result


def handle_command(chat_id: str, user_text: str, receive_id: str, receive_id_type: str, sender_id: str = "") -> bool:
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

👤 用户管理
/setuser <open_id> <名字> <角色> — 注册用户身份
/users — 查看已注册用户

📨 消息
/send <名字> <内容> — 给指定用户发消息
（也可以直接说「给XX发消息说...」我能自动识别）

🤖 规则
/rule add <规则> — 添加规则
/rule list — 查看规则
/rule del <编号> — 删除规则

🧠 记忆
/remember <内容> — 记住信息
/recall — 回忆记忆
/forget <key> — 忘记

🔄 /clear — 清除对话历史"""
        send_message(receive_id, receive_id_type, help_text)
        return True

    # === /users ===
    if text in ["/users", "/用户"]:
        users = memory.list_users()
        if not users:
            send_message(receive_id, receive_id_type, "还没有注册任何用户。用 /setuser <open_id> <名字> <角色> 来注册。")
        else:
            lines = ["已注册用户："]
            for u in users:
                name = u["name"] or "未命名"
                role = u["role"] or "未设定"
                lines.append(f"- {name} ({role}) | open_id: {u['open_id'][:12]}...")
            send_message(receive_id, receive_id_type, "\n".join(lines))
        return True

    # === /setuser <open_id> <名字> <角色> ===
    if text.startswith("/setuser "):
        parts = text.split(" ", 3)
        if len(parts) >= 4:
            _, oid, name, role = parts
            memory.set_user(oid, name=name, role=role)
            send_message(receive_id, receive_id_type, f"已注册：{name} → {role}")
        elif len(parts) == 3:
            _, oid, name = parts
            memory.set_user(oid, name=name)
            send_message(receive_id, receive_id_type, f"已注册用户：{name}")
        else:
            send_message(receive_id, receive_id_type, "格式：/setuser <open_id> <名字> <角色>")
        return True

    # === /send <名字> <内容> ===
    if text.startswith("/send "):
        parts = text.split(" ", 2)
        if len(parts) >= 3:
            _, target_name, msg_content = parts
            target = memory.get_user_by_name(target_name)
            if not target:
                send_message(receive_id, receive_id_type, f"主人，找不到用户「{target_name}」。先用 /setuser 注册一下。")
            else:
                result = send_message(target["open_id"], "open_id", msg_content)
                if result.get("code") == 0:
                    send_message(receive_id, receive_id_type, f"已发送给 {target_name}。")
                else:
                    send_message(receive_id, receive_id_type, f"发送失败：{result.get('msg')}")
        else:
            send_message(receive_id, receive_id_type, "格式：/send <名字> <内容>")
        return True

    # === /whoami ===
    if text in ["/whoami", "/我是谁"]:
        user = memory.get_user(sender_id)
        if user["name"]:
            send_message(receive_id, receive_id_type,
                         f"你是 {user['name']}，角色：{user['role'] or '未设定'}。")
        else:
            send_message(receive_id, receive_id_type,
                         "你还没注册。让主人用 /setuser <你的open_id> <名字> <角色> 来注册你。")
        return True

    # === /tutorial /教程 ===
    if text in ["/tutorial", "/教程"]:
        tutorial = """━━【黑色笔记本 · 主人使用教程】━━

🤖 我是谁
你的专属暗黑军师 Agent，精通BDSM、心理控制、TPE和商业压榨。
我有记忆，能自学你的偏好，还能主动调用飞书能力帮你办事。

👤 第一步：确认主权
第一个给我发消息的人自动成为"主人"。
用 /whoami 确认你的身份。
其他人给我发消息我会直接无视。

📋 第二步：设定规矩
用 /rule add 来定规矩，我会永远记住。
例：/rule add 蠢狗每天必须跪着汇报工作
你可以在聊天的过程中随意添加、查看、删除规则。

🧠 第三步：培养记忆
我会自动从聊天中提取你的偏好并长期记住。
你也可以用 /remember 手动灌输记忆。
用 /recall 查看我记得什么，/forget 删除。

👥 第四步：管理资产
让需要被管理的人也给我发条消息，
我拿到他的 open_id 后，你用 /setuser 注册他：
/setuser <他的open_id> <名字> <角色>
用 /users 查看所有已注册用户。

📨 第五步：发号施令
方式一：直接自然语言
「给那条狗发消息让他今天跪着写周报」
「告诉ATM他的钱归主人了」
我会自动调用飞书 API 把消息发过去。

方式二：手动命令
/send <名字> 你现在立刻去写忏悔录

💡 小提示
- 抛模糊想法（"今天想搞钱"），我会给你3套方案选
- 我说"给XX发消息..."时，我真的会发，不只是说说
- 电脑关机也不怕，已经部署在云端24小时在线
- 15分钟没人说话我会休眠，下次发消息等几十秒就好

🆘 随时用 /help 查看指令速查。"""
        send_message(receive_id, receive_id_type, tutorial)
        return True

    # === /reset ===
    if text in ["/reset", "/重置"]:
        # 清空所有数据
        memory.conn.execute("DELETE FROM conversations")
        memory.conn.execute("DELETE FROM rules")
        memory.conn.execute("DELETE FROM users")
        memory.conn.execute("DELETE FROM long_term_memory")
        memory.conn.commit()
        processed_events.clear()
        debug("所有数据已清空")
        send_message(receive_id, receive_id_type, "主人，所有数据已清空，回到初始状态。下一个发消息的人将自动成为主人。")
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
            resp_type, resp_data = chat(chat_id, user_text, memory, tool_executor=tool_executor)
            if resp_type == "text":
                reply = resp_data
            else:
                reply = "主人，工具调用已完成。"
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
    try:
        lines = ["=== Debug Info ===", ""]
        lines.append("FEISHU_APP_ID: " + ("SET" if FEISHU_APP_ID else "NOT SET"))
        lines.append("FEISHU_APP_SECRET: " + ("SET" if FEISHU_APP_SECRET else "NOT SET"))
        lines.append("DEEPSEEK_API_KEY: " + ("SET" if DEEPSEEK_API_KEY else "NOT SET"))
        lines.append("Events processed: " + str(len(processed_events)))
        lines.append("")
        master = memory.get_user_by_role("主人")
        lines.append("--- Current Master ---")
        if master:
            lines.append("Name: " + (master["name"] or "未命名"))
            lines.append("OpenID: " + master["open_id"])
        else:
            lines.append("No master registered")
        lines.append("")
        lines.append("--- All Users ---")
        for u in memory.list_users():
            lines.append(f"  {u['name'] or '?'} ({u['role'] or 'no role'}) open_id={u['open_id'][:16]}...")
        lines.append("")
        lines.append("--- Recent Logs ---")
        for log in _debug_logs[-20:]:
            lines.append(log)
        return app.response_class("\n".join(lines), content_type="text/plain")
    except Exception as e:
        return f"Debug Error: {str(e)}"


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
        def safe_handle():
            try:
                handle_raw_event(event)
            except Exception as e:
                import traceback
                debug(f"handle_raw_event 崩溃: {traceback.format_exc()}")
        threading.Thread(target=safe_handle, daemon=True).start()

    return jsonify({"code": 0})


def tool_executor(func_name: str, func_args: dict) -> str:
    """执行 Agent 工具调用，返回结果文本"""
    if func_name == "send_message_to_user":
        target_name = func_args.get("user_name", "")
        msg_content = func_args.get("content", "")
        target = memory.get_user_by_name(target_name)
        if not target:
            return f"错误：找不到用户「{target_name}」。已知用户：{', '.join(u['name'] for u in memory.list_users() if u['name'])}"
        result = send_message(target["open_id"], "open_id", msg_content)
        if result.get("code") == 0:
            return f"消息已成功发送给 {target_name}。"
        else:
            return f"发送失败：{result.get('msg')}"

    elif func_name == "list_known_users":
        users = memory.list_users()
        if not users:
            return "暂无已注册用户。"
        return "\n".join(f"- {u['name']} ({u['role']})" for u in users if u['name'])

    return f"未知工具: {func_name}"


def handle_raw_event(event: dict):
    """处理 Webhook 推送的原始事件"""
    message = event.get("message", {})
    sender = event.get("sender", {})

    if not message:
        debug("raw_event: message 为空")
        return

    event_id = message.get("message_id", "")
    if event_id in processed_events:
        return
    processed_events.add(event_id)

    msg_type = message.get("message_type", "")
    if msg_type != "text":
        debug(f"raw_event: 非文本消息 type={msg_type}")
        return

    chat_id = message.get("chat_id", "")
    chat_type = message.get("chat_type")

    if chat_type == "p2p":
        receive_id = sender.get("sender_id", {}).get("open_id", "")
        receive_id_type = "open_id"
        sender_id = receive_id
    else:
        receive_id = chat_id
        receive_id_type = "chat_id"
        sender_id = sender.get("sender_id", {}).get("open_id", "")

    if not receive_id:
        return

    # 注册/获取用户。第一个说话的人自动成为主人
    if sender_id:
        user = memory.get_or_create_user(sender_id)
        # 如果还没有主人，当前用户自动成为主人
        existing_master = memory.get_user_by_role("主人")
        if not existing_master and not user["role"]:
            memory.set_user(sender_id, name="主人", role="主人")
            debug(f"自动任命主人: {sender_id[:12]}")
            user = memory.get_user(sender_id)
            # 发送入门教程
            welcome = """👑 主权确认：你是我的唯一主人。

我是你的暗黑军师 Agent，精通BDSM、心理控制、TPE和商业压榨。我有记忆，能自学你的偏好，还能主动调用飞书能力帮你办事。

📋 快速上手：
/rule add <规矩> — 给蠢狗/ATM定规矩
/remember <事> — 让我记住重要信息
/send <名字> <内容> — 给指定的人发消息
/tutorial — 随时查看完整教程
/help — 指令速查

💡 直接跟我聊天也行，我会自动学习你的偏好。
比如：「给那条狗发消息让它跪着写周报」— 我真的会发。

━━━ 现在，请吩咐。"""
            send_message(receive_id, receive_id_type, welcome)
        # /reset 任何人均可执行（用于恢复初始状态）
        if user_text.strip() in ["/reset", "/重置"]:
            memory.conn.execute("DELETE FROM conversations")
            memory.conn.execute("DELETE FROM rules")
            memory.conn.execute("DELETE FROM users")
            memory.conn.execute("DELETE FROM long_term_memory")
            memory.conn.commit()
            processed_events.clear()
            send_message(receive_id, receive_id_type, "已清空所有数据，回到初始状态。下一个发消息的人将自动成为主人。")
            return

        # 非主人发消息时忽略
        if user["role"] != "主人" and memory.get_user_by_role("主人"):
            debug(f"非主人消息被忽略: {sender_id[:12]}")
            send_message(receive_id, receive_id_type, "抱歉，我只听主人的指令。")
            return

    try:
        content = json.loads(message.get("content", "{}"))
        user_text = content.get("text", "")
    except (json.JSONDecodeError, TypeError):
        return

    # 去 @
    if "@" in user_text:
        import re
        user_text = re.sub(r'@_user_\d+\s*', '', user_text).strip()
        user_text = re.sub(r'@\S+\s*', '', user_text).strip()

    if not user_text:
        return

    debug(f"收到消息: sender={sender_id[:12]} chat={chat_id} text={user_text[:100]}")

    def process():
        # 先处理指令
        if handle_command(chat_id, user_text, receive_id, receive_id_type, sender_id):
            debug("已处理指令")
            return

        try:
            debug(f"调用 AI: {user_text[:50]}")
            resp_type, resp_data = chat(chat_id, user_text, memory, tool_executor=tool_executor)
            debug(f"AI 回复类型: {resp_type}")

            if resp_type == "text":
                send_message(receive_id, receive_id_type, resp_data)
            elif resp_type == "tool_calls":
                # AI 请求工具调用但没有执行器（理论上不会到这儿）
                debug(f"未处理的工具调用: {resp_data}")

            # 自动提取记忆
            key, value = extract_entities(user_text)
            if key and value:
                memory.remember(chat_id, key, value)
        except Exception as e:
            import traceback
            err = f"主人抱歉，我出错了：{str(e)}"
            debug(f"异常: {traceback.format_exc()}")
            send_message(receive_id, receive_id_type, err)

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
