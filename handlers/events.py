"""事件处理器 — 统一的消息处理流水线"""
import json
import logging
import re
import threading

from services.ai_client import chat, extract_entities

logger = logging.getLogger(__name__)


class EventHandler:
    def __init__(self, memory, feishu_client, command_handler):
        self.memory = memory
        self.client = feishu_client
        self.commands = command_handler
        self.processed_events = set()  # 去重

    # ============================================================
    # 统一处理入口
    # ============================================================

    def process_webhook_event(self, event: dict):
        """处理 Webhook 推送的原始事件字典（Render 云端路径）"""
        message = event.get("message", {})
        sender = event.get("sender", {})

        if not message:
            logger.info("webhook: message 为空")
            return

        event_id = message.get("message_id", "")
        if event_id in self.processed_events:
            return
        self.processed_events.add(event_id)

        msg_type = message.get("message_type", "")
        if msg_type != "text":
            logger.info(f"webhook: 非文本消息 type={msg_type}")
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

        # 解析消息文本
        user_text = self._extract_text(message)
        if not user_text:
            return

        logger.info(f"[消息] sender={sender_id[:12] if sender_id else '?'} chat={chat_id} text={user_text[:100]}")

        # 在新线程中处理
        threading.Thread(
            target=self._process_message,
            args=(chat_id, user_text, receive_id, receive_id_type, sender_id),
            daemon=True,
        ).start()

    def on_ws_message(self, event):
        """处理长连接消息事件（本地开发路径）"""
        from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

        if not isinstance(event, P2ImMessageReceiveV1):
            return

        event_data = event.event
        if not event_data:
            return

        message = event_data.message
        sender = event_data.sender

        if not message or not sender:
            return

        event_id = message.message_id
        if event_id in self.processed_events:
            return
        self.processed_events.add(event_id)

        if message.message_type != "text":
            return

        chat_id = message.chat_id or ""
        chat_type = message.chat_type

        if chat_type == "p2p":
            receive_id = sender.sender_id.open_id if sender.sender_id else ""
            receive_id_type = "open_id"
            sender_id = receive_id
        else:
            receive_id = chat_id
            receive_id_type = "chat_id"
            sender_id = sender.sender_id.open_id if sender.sender_id else ""

        if not receive_id:
            return

        user_text = self._extract_text(message)
        if not user_text:
            return

        logger.info(f"[WS消息] sender={sender_id[:12] if sender_id else '?'} chat={chat_id} text={user_text[:100]}")

        threading.Thread(
            target=self._process_message,
            args=(chat_id, user_text, receive_id, receive_id_type, sender_id),
            daemon=True,
        ).start()

    # ============================================================
    # 核心处理逻辑
    # ============================================================

    def _process_message(self, chat_id: str, user_text: str, receive_id: str,
                         receive_id_type: str, sender_id: str):
        """统一的消息处理流水线"""
        try:
            # 1. 自动注册用户 + 判断主人
            should_process, skip_reason = self._check_access(sender_id, user_text, receive_id, receive_id_type)
            if not should_process:
                return

            # 2. 尝试指令处理
            if self.commands.handle(chat_id, user_text, receive_id, receive_id_type, sender_id):
                logger.info("已通过指令处理")
                return

            # 3. AI 对话
            logger.info(f"调用 AI: {user_text[:50]}")
            resp_type, resp_data = chat(chat_id, user_text, self.memory)

            if resp_type == "text":
                self._send_reply(receive_id, receive_id_type, resp_data)
            elif resp_type == "tool_calls":
                logger.info(f"AI 请求工具调用: {resp_data}")

            # 4. 自动提取记忆
            key, value = extract_entities(user_text)
            if key and value:
                self.memory.remember(chat_id, key, value)

        except Exception as e:
            import traceback
            err = f"主人抱歉，我出错了：{str(e)}"
            logger.error(f"处理消息异常: {traceback.format_exc()}")
            try:
                self.client.send_text_message(receive_id, receive_id_type, err)
            except Exception:
                pass

    def _check_access(self, sender_id: str, user_text: str, receive_id: str,
                      receive_id_type: str) -> tuple:
        """检查发送者权限。返回 (should_process: bool, reason: str)。"""
        if not sender_id:
            return True, ""  # 无 sender_id 时放行

        # 注册/获取用户
        user = self.memory.get_or_create_user(sender_id)

        # 如果还没有主人，当前用户自动成为主人
        existing_master = self.memory.get_user_by_role("主人")
        if not existing_master and not user["role"]:
            self.memory.set_user(sender_id, name="主人", role="主人")
            logger.info(f"自动任命主人: {sender_id[:12]}...")

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
            self.client.send_text_message(receive_id, receive_id_type, welcome)

        # /reset 任何人均可执行（恢复初始状态）
        if user_text.strip() in ["/reset", "/重置"]:
            self.memory.reset_all()
            self.processed_events.clear()
            self.client.send_text_message(
                receive_id, receive_id_type,
                "已清空所有数据，回到初始状态。下一个发消息的人将自动成为主人。"
            )
            return False, "reset_executed"

        # 非主人发消息时忽略（前提是已经有主人）
        if user["role"] != "主人" and self.memory.get_user_by_role("主人"):
            logger.info(f"非主人消息被忽略: {sender_id[:12]}...")
            self.client.send_text_message(receive_id, receive_id_type,
                                          "抱歉，我只听主人的指令。")
            return False, "not_master"

        return True, ""

    # ============================================================
    # 辅助方法
    # ============================================================

    def _extract_text(self, message) -> str:
        """从消息对象中提取文本内容（兼容 dict 和 SDK model）"""
        if isinstance(message, dict):
            try:
                content = json.loads(message.get("content", "{}"))
                user_text = content.get("text", "")
            except (json.JSONDecodeError, TypeError):
                return ""
        else:
            try:
                content = json.loads(message.content or "{}")
                user_text = content.get("text", "")
            except (json.JSONDecodeError, TypeError):
                return ""

        # 去除 @ 机器人的部分
        if "@" in user_text:
            user_text = re.sub(r'@_user_\d+\s*', '', user_text).strip()
            user_text = re.sub(r'@\S+\s*', '', user_text).strip()

        return user_text

    def _send_reply(self, receive_id: str, receive_id_type: str, reply: str):
        """发送回复（自动处理过长消息的分片）"""
        # 飞书单条消息限制约 30000 字节
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
                self.client.send_text_message(receive_id, receive_id_type, chunk)
        else:
            self.client.send_text_message(receive_id, receive_id_type, reply)
