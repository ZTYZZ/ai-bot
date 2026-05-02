import json
import os
import threading
import time
import logging
from flask import Flask, request, jsonify

from config import (
    FEISHU_APP_ID,
    FEISHU_APP_SECRET,
    FEISHU_VERIFY_TOKEN,
    DEEPSEEK_API_KEY,
    QQ_APP_ID,
    QQ_APP_SECRET,
)
from db.memory import Memory
from services.feishu_client import FeishuClient
from services.qq_client import QQClient
from services.cron_agent import run_autonomy_check
from handlers.commands import CommandHandler
from handlers.events import EventHandler
from handlers.qq_events import QQEventHandler
import tools.context as tool_ctx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ============================================================
# 调试日志
# ============================================================
_debug_logs = []  # type: list
_qq_webhook_logs = []  # 最近 QQ webhook 请求记录，用于调试验证问题


def debug(msg: str):
    """记录调试日志"""
    ts = time.strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    _debug_logs.append(entry)
    if len(_debug_logs) > 50:
        _debug_logs.pop(0)
    logger.info(msg)


# ============================================================
# 初始化依赖（模块级别，供 tools 懒加载访问）
# ============================================================
memory = Memory()

# 一次性绑定 QQ 身份（部署即执行，失败不阻塞启动）
try:
    # 使用 set_user（upsert）而非 bind_qq_to_user，因为启动时用户记录可能还不存在
    memory.set_user(open_id="ou_90a4f71b6", qq_id="B2A76444143B0CC0DAB8C76D407F047C")
    memory.set_user(open_id="ou_9f8451598dc4ba5aca244846781c1b9f", qq_id="10B173E5FB2EF6D26C93D78CC9A0FB3F")
    logger.info("QQ绑定: 已预写入 open_id ↔ QQ ID")
    debug("QQ绑定: 已预写入 open_id ↔ QQ ID")
except Exception as e:
    logger.warning(f"QQ绑定异常(非致命): {e}")

feishu_client = FeishuClient()
qq_client = QQClient()
qq_client.set_debug(debug)
tool_ctx.set_memory(memory)
tool_ctx.set_feishu_client(feishu_client)
tool_ctx.set_qq_client(qq_client)
command_handler = CommandHandler(memory, feishu_client)
event_handler = EventHandler(memory, feishu_client, command_handler, debug_func=debug, qq_client=qq_client)
qq_event_handler = QQEventHandler(memory, qq_client, feishu_client=feishu_client, debug_func=debug)


# ============================================================
# Flask 应用
# ============================================================
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
        lines.append("DATABASE_URL: " + ("SET" if os.getenv("DATABASE_URL") else "NOT SET (using SQLite)"))
        lines.append("Events processed: " + str(len(event_handler.processed_events)))
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
        for log_entry in _debug_logs[-20:]:
            lines.append(log_entry)
        lines.append("")
        lines.append("--- QQ Webhook Requests ---")
        if _qq_webhook_logs:
            for ql in _qq_webhook_logs[-10:]:
                lines.append(ql)
        else:
            lines.append("(no QQ webhook requests received yet)")

        return app.response_class("\n".join(lines), content_type="text/plain")
    except Exception as e:
        return f"Debug Error: {str(e)}"


@app.route("/cron")
def cron_check():
    """定时巡航端点 — 由外部 cron 服务（如 cron-job.org）定期触发"""
    try:
        report = run_autonomy_check(memory, feishu_client)
        debug(f"巡航报告: {report[:200]}")
        return jsonify({"code": 0, "report": report})
    except Exception as e:
        import traceback
        logger.error(f"巡航异常: {traceback.format_exc()}")
        return jsonify({"code": -1, "error": str(e)})


@app.route("/qq_webhook", methods=["GET", "POST"])
def qq_webhook():
    """QQ 机器人 Webhook 接收端点"""
    ts = time.strftime("%H:%M:%S")
    body_raw = request.get_data(as_text=True)[:500]
    _qq_webhook_logs.append(f"[{ts}] {request.method} body={body_raw}")
    if len(_qq_webhook_logs) > 20:
        _qq_webhook_logs.pop(0)
    logger.info(f"[QQ_WEBHOOK] method={request.method}, headers={dict(request.headers)}, args={dict(request.args)}, body={body_raw}")

    # QQ Webhook 握手验证（op=10 Hello）
    if request.method == "POST":
        body = request.get_json(silent=True) or {}

        # op=10: Hello (WebSocket 握手包，验证 URL 时平台可能发这个)
        if body.get("op") == 10:
            plain_token = body.get("d", {}).get("plain_token", "")
            logger.info(f"[QQ_WEBHOOK] op=10 plain_token={plain_token}")
            return jsonify({"plain_token": plain_token})

        # op=13: 回调地址验证（Ed25519 签名）
        if body.get("op") == 13:
            d = body.get("d", {})
            plain_token = d.get("plain_token", "")
            event_ts = d.get("event_ts", "")
            # Ed25519 签名：用 AppSecret 派生 seed → 私钥 → 签名(event_ts + plain_token)
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            secret = QQ_APP_SECRET or ""
            # Seed = AppSecret 重复到 32 字节后截断
            seed_str = secret * ((32 // max(len(secret), 1)) + 1)
            seed = seed_str[:32].encode("utf-8")
            private_key = Ed25519PrivateKey.from_private_bytes(seed)
            msg = f"{event_ts}{plain_token}".encode("utf-8")
            sig = private_key.sign(msg).hex()
            logger.info(f"[QQ_WEBHOOK] op=13 plain_token={plain_token[:20]} sig_hex={sig[:20]}")
            return jsonify({"plain_token": plain_token, "signature": sig})

        # 事件处理（op=0）
        if body.get("op") == 0:
            # 先回复 ACK 确认收件（QQ 平台要求），再异步处理
            threading.Thread(
                target=_safe_handle_qq,
                args=(body,),
                daemon=True,
            ).start()
            return jsonify({"op": 12})


def _safe_handle_qq(body: dict):
    """安全处理 QQ webhook 事件"""
    try:
        qq_event_handler.process_webhook(body)
    except Exception as e:
        import traceback
        debug(f"QQ webhook 处理崩溃: {traceback.format_exc()}")


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
        threading.Thread(
            target=_safe_handle_webhook,
            args=(event,),
            daemon=True,
        ).start()

    return jsonify({"code": 0})


def _safe_handle_webhook(event: dict):
    """安全处理 webhook 事件，捕获所有异常"""
    try:
        event_handler.process_webhook_event(event)
    except Exception as e:
        import traceback
        debug(f"webhook 处理崩溃: {traceback.format_exc()}")


# ============================================================
# 长连接客户端（本地开发模式）
# ============================================================

def start_ws_client():
    from lark_oapi.ws import Client as WSClient
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

    handler = (
        EventDispatcherHandler
        .builder("", FEISHU_VERIFY_TOKEN)
        .register_p2_im_message_receive_v1(event_handler.on_ws_message)
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


# 云端模式（有 PORT 或 RENDER 环境变量）→ 只用 Webhook
# 本地模式 → 启动长连接
if not os.getenv("RENDER") and not os.getenv("PORT"):
    logger.info("本地模式：启动飞书长连接")
    ws_thread = threading.Thread(target=start_ws_client, daemon=True)
    ws_thread.start()
else:
    logger.info("云端模式：使用 Webhook 接收消息")


# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    print("=" * 50)
    print("  🤖 AI 助手启动中...")
    print("=" * 50)
    port = int(os.getenv("PORT", 8080))
    logger.info(f"健康检查端口: {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
