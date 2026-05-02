"""QQ 事件处理器 — 解析 QQ Webhook 推送，复用同一 AI 管道"""
import json
import logging
import threading

from services.ai_client import chat

logger = logging.getLogger(__name__)


class QQEventHandler:
    def __init__(self, memory, qq_client, feishu_client=None, debug_func=None):
        self.memory = memory
        self.qq = qq_client
        self.feishu = feishu_client
        self.processed_events = set()
        self._debug = debug_func or (lambda m: logger.info(m))

    def process_webhook(self, body: dict):
        """处理 QQ Webhook POST 请求体"""
        op = body.get("op", -1)
        self._debug(f"[QQ] webhook op={op}")

        # op=10: Hello (握手包，需要返回验证)
        # op=11: Heartbeat ACK
        # op=13: Callback verification
        if op == 10:
            return None  # 由 webhook 端点处理验证

        if op != 0:
            self._debug(f"[QQ] 跳过非事件 op={op}")
            return

        event_type = body.get("t", "")
        data = body.get("d", {})
        event_id = body.get("id", "")

        if event_id in self.processed_events:
            return
        self.processed_events.add(event_id)

        author = data.get("author", {})
        sender_id = author.get("id", "")
        content = data.get("content", "").strip()

        if not sender_id or not content:
            return

        self._debug(f"[QQ] 消息: sender={sender_id[:12]} type={event_type} text={content[:80]}")

        # 确定回复方式和 receive_id
        if event_type == "C2C_MESSAGE_CREATE":
            receive_id = sender_id
            is_group = False
        elif event_type == "GROUP_AT_MESSAGE_CREATE":
            receive_id = data.get("group_openid", "")
            is_group = True
            if not receive_id:
                return
        else:
            self._debug(f"[QQ] 不支持的事件类型: {event_type}")
            return

        # 在新线程中处理
        threading.Thread(
            target=self._process,
            args=(sender_id, content, receive_id, is_group),
            daemon=True,
        ).start()

    def _process(self, sender_id: str, user_text: str, receive_id: str, is_group: bool):
        """统一处理流水线"""
        try:
            # 1. 获取或创建 QQ 用户
            user = self.memory.get_or_create_user_by_qq(sender_id)

            # 2. 权限检查
            master = self.memory.get_user_by_role("主人")
            has_master = bool(master)
            user_role = user.get("role", "")

            # 如果还没有主人，当前用户自动成为主人
            if not has_master and not user_role:
                self.memory.set_user(qq_id=sender_id, name="主人（QQ）", role="主人")
                self._debug(f"[QQ] 自动任命 QQ 用户为主人: {sender_id[:12]}")
                self.qq.send_text_message(
                    receive_id,
                    "👑 主权确认：你是我的唯一主人（QQ 端）。\n"
                    "飞书和 QQ 共享同一份数据、同一份身份。\n"
                    "如果你也有飞书账号，在飞书上对我说「绑定QQ：" + sender_id + "」来关联两个平台的身份。",
                    is_group=is_group,
                )
                return

            # 未注册且已有主人
            if not user_role and has_master:
                master_qq = master.get("qq_id", "")
                if not master_qq:
                    # 主人尚无 QQ 绑定 → 自动将此 QQ 绑定到主人
                    master_oid = master.get("open_id", "")
                    if master_oid:
                        ok = self.memory.bind_qq_to_user(master_oid, sender_id)
                        self._debug(f"[QQ] 自动绑定QQ到主人: {'OK' if ok else 'FAIL'} master_oid={master_oid[:16]} qq={sender_id[:12]}")
                        if ok:
                            # 重新获取用户身份（现在是主人了）
                            user = self.memory.get_or_create_user_by_qq(sender_id)
                            user_role = user.get("role", "")
                            self.qq.send_text_message(
                                receive_id,
                                "✅ QQ 已自动绑定到你的主人身份。飞书和 QQ 现在共享同一身份。",
                                is_group=is_group,
                            )
                        else:
                            self.qq.send_text_message(
                                receive_id,
                                f"⚠️ 自动绑定失败。请先在飞书上给 AI 发一条消息以创建用户记录，然后重试。\n"
                                f"你的 QQ ID：{sender_id}",
                                is_group=is_group,
                            )
                            return
                    else:
                        self.qq.send_text_message(
                            receive_id,
                            f"⚠️ 系统异常：主人没有 open_id，无法绑定。",
                            is_group=is_group,
                        )
                        return
                else:
                    # 主人已有 QQ 绑定，但不是这个人 → 拒绝
                    self._debug(f"[QQ] 未注册用户被拒绝: {sender_id[:12]}")
                    self.qq.send_text_message(
                        receive_id,
                        f"抱歉，我只听主人的指令。\n你的 QQ ID 是：{sender_id}\n请将 ID 发给主人注册。",
                        is_group=is_group,
                    )
                    return

            # /reset 命令
            if user_text.strip() in ["/reset", "/重置"]:
                self.memory.reset_all()
                self.processed_events.clear()
                self.qq.send_text_message(receive_id, "已清空所有数据，回到初始状态。", is_group=is_group)
                return

            # 3. 如果用户绑定了飞书，用飞书 open_id；否则用 QQ id
            feishu_open_id = user.get("open_id") or ""

            # 4. AI 对话 — 复用同一管道
            self._debug(f"[QQ] 调用 AI: {user_text[:50]}")
            resp_type, resp_data = chat(
                sender_id,  # chat_id
                user_text,
                self.memory,
                sender_id=feishu_open_id if feishu_open_id else None,
                qq_sender_id=sender_id,
            )

            self._debug(f"[QQ] AI 返回: type={resp_type}")
            if resp_type == "text":
                self._send_qq_reply(receive_id, resp_data, is_group)
                # 跨平台：用户绑定了飞书则同步推送
                self._forward_to_feishu(user, resp_data)
                # 资产消息 → 自动通知主人
                self._notify_master_on_asset_msg(sender_id, user, user_text, resp_data)

        except Exception as e:
            import traceback
            self._debug(f"[QQ] 处理异常: {traceback.format_exc()}")
            logger.error(f"[QQ] 处理异常: {traceback.format_exc()}")

    def _send_qq_reply(self, receive_id: str, reply: str, is_group: bool):
        """发送 QQ 回复（自动分片过长消息）"""
        max_len = 2000  # QQ 单条消息限制约 2000 字符
        if len(reply) <= max_len:
            result = self.qq.send_text_message(receive_id, reply, is_group=is_group)
            self._debug(f"[QQ] 发送结果: code={result.get('code')}, msg={result.get('msg', '')[:80]}")
        else:
            chunks = []
            current = ""
            for line in reply.split("\n"):
                if len(current + line) > max_len:
                    chunks.append(current)
                    current = line + "\n"
                else:
                    current += line + "\n"
            if current:
                chunks.append(current)
            for chunk in chunks:
                result = self.qq.send_text_message(receive_id, chunk, is_group=is_group)
                self._debug(f"[QQ] 发送结果: code={result.get('code')}, msg={result.get('msg', '')[:80]}")

    def _forward_to_feishu(self, user: dict, reply: str):
        """如果 QQ 用户绑定了飞书，同步推送回复到飞书"""
        if not self.feishu:
            return
        feishu_oid = user.get("open_id", "")
        if not feishu_oid:
            return
        try:
            self.feishu.send_text_message(feishu_oid, "open_id", reply)
            self._debug(f"[QQ] 已同步转发到飞书: {feishu_oid[:16]}")
        except Exception as e:
            logger.warning(f"[QQ] 飞书转发失败: {e}")

    def _notify_master_on_asset_msg(self, sender_id: str, user: dict, user_text: str, resp_data: str):
        """资产发消息时，自动通知主人（应用层兜底，不依赖 AI 自觉调用工具）"""
        user_role = user.get("role", "")
        if not user_role or user_role == "主人":
            return

        master = self.memory.get_user_by_role("主人")
        if not master:
            return

        user_name = user.get("name", sender_id[:12])
        notify = f"📨 资产「{user_name}」发来消息(QQ)\n\n💬 资产说：{user_text[:200]}\n\n🤖 我的回复：{resp_data[:300]}"

        # QQ 通知主人
        master_qq = master.get("qq_id", "")
        if master_qq:
            try:
                self._send_qq_reply(master_qq, notify, is_group=False)
                self._debug(f"[QQ] 已自动通知主人(QQ): {user_name}")
            except Exception as e:
                logger.warning(f"[QQ] 通知主人(QQ)失败: {e}")

        # 飞书通知主人
        master_oid = master.get("open_id", "")
        if self.feishu and master_oid:
            try:
                self.feishu.send_text_message(master_oid, "open_id", notify)
                self._debug(f"[QQ] 已自动通知主人(飞书): {user_name}")
            except Exception as e:
                logger.warning(f"[QQ] 通知主人(飞书)失败: {e}")
