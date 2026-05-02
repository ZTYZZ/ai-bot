"""事件处理器 — 统一的消息处理流水线"""
import json
import logging
import re
import threading

from services.ai_client import chat, extract_entities

logger = logging.getLogger(__name__)


class EventHandler:
    def __init__(self, memory, feishu_client, command_handler, debug_func=None, qq_client=None):
        self.memory = memory
        self.client = feishu_client
        self.qq = qq_client
        self.commands = command_handler
        self.processed_events = set()  # 去重
        self._debug = debug_func or (lambda m: logger.info(m))

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
            # 群聊：仅当被 @ 时才响应
            mentions = message.get("mentions", [])
            if not mentions:
                logger.info("webhook: 群聊未 @ 机器人，跳过")
                return
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
            # 群聊：仅当被 @ 时才响应
            mentions = getattr(message, "mentions", []) or []
            if not mentions:
                logger.info("WS: 群聊未 @ 机器人，跳过")
                return
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
            self._debug(f"开始处理消息: {user_text[:80]}")
            # 1. 自动注册用户 + 判断主人
            should_process, skip_reason = self._check_access(sender_id, user_text, receive_id, receive_id_type)
            if not should_process:
                self._debug(f"消息被过滤: {skip_reason}")
                return

            # 2. 尝试指令处理
            if self.commands.handle(chat_id, user_text, receive_id, receive_id_type, sender_id):
                self._debug("已通过指令处理")
                return

            # 3. AI 对话
            self._debug(f"调用 AI: {user_text[:50]}")
            resp_type, resp_data = chat(chat_id, user_text, self.memory, sender_id=sender_id)

            self._debug(f"AI 返回: type={resp_type}, data_len={len(str(resp_data))}")
            if resp_type == "text":
                self._send_reply(receive_id, receive_id_type, resp_data)
                # 跨平台：发送者绑定了 QQ 则同步推送
                self._forward_to_qq(sender_id, resp_data)
                # 资产消息 → 自动通知主人（不依赖 AI 自觉）
                self._notify_master_on_asset_msg(sender_id, user_text, resp_data)
            elif resp_type == "tool_calls":
                self._debug(f"AI 请求工具调用: {resp_data}")

            # 4. 自动提取记忆
            key, value = extract_entities(user_text)
            if key and value:
                self.memory.remember(chat_id, key, value)

        except Exception as e:
            import traceback
            self._debug(f"处理消息异常: {traceback.format_exc()}")
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

        # 权限判断
        has_master = bool(self.memory.get_user_by_role("主人"))

        # 未注册 + 已有主人 → 拒绝
        if not user["role"] and has_master:
            logger.info(f"未注册用户消息被忽略: {sender_id[:12]}...")
            self.client.send_text_message(
                receive_id, receive_id_type,
                f"抱歉，我只听主人的指令。\n\n你的 open_id 是：{sender_id}\n请将这个 open_id 发给主人，让主人用 /setuser {sender_id} <名字> <角色> 来注册你。"
            )
            return False, "unregistered"

        # 已注册的资产/其他角色 → 允许对话（用于任务汇报）
        if user["role"] not in ("主人", "") and has_master:
            # 更新用户名（飞书可能不传名字）
            self._debug(f"资产用户发言: {user.get('name', '未知')} ({sender_id[:12]}...)")
            return True, ""

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
        def send_one(chunk):
            result = self.client.send_text_message(receive_id, receive_id_type, chunk)
            self._debug(f"发送结果: code={result.get('code')}, msg={result.get('msg')}")
            return result

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
                send_one(chunk)
        else:
            send_one(reply)

    def _forward_to_qq(self, sender_id: str, reply: str):
        """如果发送者绑定了 QQ，同步推送回复到 QQ"""
        if not self.qq:
            return
        user = self.memory.get_user(sender_id)
        qq_id = user.get("qq_id", "")
        if not qq_id:
            return
        try:
            self._send_qq_chunks(qq_id, reply, is_group=False)
        except Exception as e:
            logger.warning(f"QQ 转发失败: {e}")

    def _notify_master_on_asset_msg(self, sender_id: str, user_text: str, resp_data: str):
        """资产发消息时，自动通知主人（应用层兜底，不依赖 AI 自觉调用工具）"""
        user = self.memory.get_user(sender_id)
        user_role = user.get("role", "")
        if not user_role or user_role == "主人":
            return  # 主人自己发消息不通知

        master = self.memory.get_user_by_role("主人")
        if not master:
            return

        user_name = user.get("name", sender_id[:12])
        notify = f"📨 资产「{user_name}」发来消息\n\n💬 资产说：{user_text[:200]}\n\n🤖 我的回复：{resp_data[:300]}"

        # 飞书通知主人
        master_oid = master.get("open_id", "")
        if master_oid:
            try:
                self.client.send_text_message(master_oid, "open_id", notify)
                self._debug(f"已自动通知主人(飞书): {user_name}")
            except Exception as e:
                logger.warning(f"通知主人(飞书)失败: {e}")

        # QQ 通知主人
        master_qq = master.get("qq_id", "")
        if self.qq and master_qq:
            try:
                self._send_qq_chunks(master_qq, notify, is_group=False)
                self._debug(f"已自动通知主人(QQ): {user_name}")
            except Exception as e:
                logger.warning(f"通知主人(QQ)失败: {e}")

    def _send_qq_chunks(self, qq_id: str, text: str, is_group: bool = False):
        """通过 QQ 发送消息（自动分片，QQ 单条限制约 2000 字符）"""
        if not self.qq:
            return
        max_len = 2000
        if len(text) <= max_len:
            self.qq.send_text_message(qq_id, text, is_group=is_group)
        else:
            chunks = []
            current = ""
            for line in text.split("\n"):
                if len(current + line) > max_len:
                    chunks.append(current)
                    current = line + "\n"
                else:
                    current += line + "\n"
            if current:
                chunks.append(current)
            for chunk in chunks:
                self.qq.send_text_message(qq_id, chunk, is_group=is_group)
