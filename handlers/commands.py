"""指令处理器 — 所有 / 开头的指令"""
from services.ai_client import extract_entities


class CommandHandler:
    def __init__(self, memory, feishu_client):
        self.memory = memory
        self.client = feishu_client

    def handle(self, chat_id: str, user_text: str, receive_id: str,
               receive_id_type: str, sender_id: str = "") -> bool:
        """处理指令，返回 True 表示已处理。"""
        text = user_text.strip()

        # === /rule add <规则> ===
        if text.startswith("/rule add ") or text.startswith("/规则 add "):
            rule_content = text.split("add ", 1)[1].strip()
            if rule_content:
                rule_id = self.memory.add_rule(chat_id, rule_content)
                self.client.send_text_message(
                    receive_id, receive_id_type,
                    f"好的主人，已经记住这条规则了 (编号 #{rule_id})：\n「{rule_content}」"
                )
            return True

        # === /rule list ===
        if text in ["/rule list", "/规则 list", "/rule", "/规则"]:
            all_rules = self.memory.get_rules("global") + self.memory.get_rules(chat_id)
            if not all_rules:
                self.client.send_text_message(
                    receive_id, receive_id_type,
                    "主人，目前还没有设定任何规则哦。用 /rule add <规则内容> 来添加吧。"
                )
            else:
                lines = ["主人，这是您当前设定的规则："]
                for r in all_rules:
                    scope = "全局" if r["chat_id"] == "global" else "当前会话"
                    lines.append(f"#{r['id']} [{scope}]: {r['rule']}")
                self.client.send_text_message(receive_id, receive_id_type, "\n".join(lines))
            return True

        # === /rule del <id> ===
        if text.startswith("/rule del ") or text.startswith("/规则 del "):
            try:
                rule_id = int(text.split("del ", 1)[1].strip())
                if self.memory.delete_rule(rule_id, chat_id):
                    self.client.send_text_message(receive_id, receive_id_type,
                                                  f"主人，规则 #{rule_id} 已删除。")
                else:
                    self.client.send_text_message(receive_id, receive_id_type,
                                                  f"主人，没找到规则 #{rule_id}。")
            except ValueError:
                self.client.send_text_message(receive_id, receive_id_type,
                                              "主人，格式是：/rule del <编号>")
            return True

        # === /remember <内容> ===
        if text.startswith("/remember ") or text.startswith("/记 "):
            content = text.split(" ", 1)[1].strip()
            if content:
                key, value = extract_entities(content)
                if key and value:
                    self.memory.remember(chat_id, key, value)
                    self.client.send_text_message(
                        receive_id, receive_id_type,
                        f"已记住主人：{key} = {value}"
                    )
                else:
                    self.memory.remember(
                        chat_id,
                        f"记忆_{len(self.memory.recall(chat_id)) + 1}",
                        content,
                    )
                    self.client.send_text_message(
                        receive_id, receive_id_type,
                        f"主人，我记住了：「{content}」"
                    )
            return True

        # === /recall ===
        if text in ["/recall", "/回忆"]:
            mems = self.memory.recall(chat_id)
            if not mems:
                self.client.send_text_message(
                    receive_id, receive_id_type,
                    "主人，我目前没有关于您的特别记忆。"
                )
            else:
                lines = ["主人，我记得这些："]
                for k, v in mems.items():
                    lines.append(f"- {k}: {v}")
                self.client.send_text_message(receive_id, receive_id_type,
                                              "\n".join(lines))
            return True

        # === /forget <key> ===
        if text.startswith("/forget ") or text.startswith("/忘 "):
            key = text.split(" ", 1)[1].strip()
            self.memory.forget(chat_id, key)
            self.client.send_text_message(receive_id, receive_id_type,
                                          f"主人，我忘记了「{key}」。")
            return True

        # === /clear ===
        if text in ["/clear", "/清除"]:
            self.memory.clear_conversation(chat_id)
            self.client.send_text_message(receive_id, receive_id_type,
                                          "主人，当前对话记忆已清除。")
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
            self.client.send_text_message(receive_id, receive_id_type, help_text)
            return True

        # === /users ===
        if text in ["/users", "/用户"]:
            users = self.memory.list_users()
            if not users:
                self.client.send_text_message(
                    receive_id, receive_id_type,
                    "还没有注册任何用户。用 /setuser <open_id> <名字> <角色> 来注册。"
                )
            else:
                lines = ["已注册用户："]
                for u in users:
                    name = u["name"] or "未命名"
                    role = u["role"] or "未设定"
                    lines.append(
                        f"- {name} ({role}) | open_id: {u['open_id'][:12]}..."
                    )
                self.client.send_text_message(receive_id, receive_id_type,
                                              "\n".join(lines))
            return True

        # === /setuser <open_id> <名字> <角色> ===
        if text.startswith("/setuser "):
            parts = text.split(" ", 3)
            if len(parts) >= 4:
                _, oid, name, role = parts
                self.memory.set_user(oid, name=name, role=role)
                self.client.send_text_message(receive_id, receive_id_type,
                                              f"已注册：{name} → {role}")
            elif len(parts) == 3:
                _, oid, name = parts
                self.memory.set_user(oid, name=name)
                self.client.send_text_message(receive_id, receive_id_type,
                                              f"已注册用户：{name}")
            else:
                self.client.send_text_message(
                    receive_id, receive_id_type,
                    "格式：/setuser <open_id> <名字> <角色>"
                )
            return True

        # === /send <名字> <内容> ===
        if text.startswith("/send "):
            parts = text.split(" ", 2)
            if len(parts) >= 3:
                _, target_name, msg_content = parts
                target = self.memory.get_user_by_name(target_name)
                if not target:
                    self.client.send_text_message(
                        receive_id, receive_id_type,
                        f"主人，找不到用户「{target_name}」。先用 /setuser 注册一下。"
                    )
                else:
                    result = self.client.send_text_message(
                        target["open_id"], "open_id", msg_content
                    )
                    if result.get("code") == 0:
                        self.client.send_text_message(
                            receive_id, receive_id_type,
                            f"已发送给 {target_name}。"
                        )
                    else:
                        self.client.send_text_message(
                            receive_id, receive_id_type,
                            f"发送失败：{result.get('msg')}"
                        )
            else:
                self.client.send_text_message(receive_id, receive_id_type,
                                              "格式：/send <名字> <内容>")
            return True

        # === /whoami ===
        if text in ["/whoami", "/我是谁"]:
            user = self.memory.get_user(sender_id)
            if user["name"]:
                self.client.send_text_message(
                    receive_id, receive_id_type,
                    f"你是 {user['name']}，角色：{user['role'] or '未设定'}。"
                )
            else:
                self.client.send_text_message(
                    receive_id, receive_id_type,
                    "你还没注册。让主人用 /setuser <你的open_id> <名字> <角色> 来注册你。"
                )
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
            self.client.send_text_message(receive_id, receive_id_type, tutorial)
            return True

        # === /reset ===
        if text in ["/reset", "/重置"]:
            self.memory.reset_all()
            self.client.send_text_message(
                receive_id, receive_id_type,
                "主人，所有数据已清空，回到初始状态。下一个发消息的人将自动成为主人。"
            )
            return True

        return False
