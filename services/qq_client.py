"""QQ 机器人 API 客户端"""
import json
import time
import logging
import requests

from config import QQ_APP_ID, QQ_APP_SECRET

logger = logging.getLogger(__name__)

QQ_API_BASE = "https://api.sgroup.qq.com"
QQ_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"


class QQClient:
    def __init__(self):
        self._token = ""
        self._token_expires_at = 0
        self._debug = lambda m: logger.info(m)

    def set_debug(self, debug_func):
        """注入调试日志函数，日志会显示在 /debug 页面上"""
        self._debug = debug_func

    def _get_token(self) -> str:
        """获取或刷新 access_token"""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        try:
            resp = requests.post(
                QQ_TOKEN_URL,
                json={"appId": QQ_APP_ID, "clientSecret": QQ_APP_SECRET},
                timeout=10,
            )
            data = resp.json()
            self._token = data.get("access_token", "")
            expires_in = data.get("expires_in", 7200)
            self._token_expires_at = time.time() + expires_in
            logger.info(f"[QQ] Token 获取成功, expires_in={expires_in}s, token_len={len(self._token)}")
            self._debug(f"[QQ] Token: len={len(self._token)}, expires_in={expires_in}s, full_resp={json.dumps({k:v for k,v in data.items() if k != 'access_token'}, ensure_ascii=False)}")
            return self._token
        except Exception as e:
            logger.error(f"[QQ] Token 获取失败: {e}")
            self._debug(f"[QQ] Token 获取失败: {e}")
            return ""

    def send_text_message(self, receive_id: str, content: str, is_group: bool = False) -> dict:
        """发送文本消息

        receive_id: 用户 openid（c2c）或群 openid（group）
        is_group: True = 群消息，False = 单聊
        """
        token = self._get_token()
        if not token:
            self._debug(f"[QQ] send_text_message: token 获取失败")
            return {"code": -1, "msg": "QQ token 获取失败"}

        if is_group:
            url = f"{QQ_API_BASE}/v2/groups/{receive_id}/messages"
        else:
            url = f"{QQ_API_BASE}/v2/users/{receive_id}/messages"

        headers = {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json",
        }
        import uuid
        body = {
            "content": content,
            "msg_type": 0,  # 0 = 文本
            "msg_id": str(uuid.uuid4()),
        }

        self._debug(f"[QQ] 发送消息: url={url}, content_len={len(content)}, token_len={len(token)}")

        try:
            resp = requests.post(url, headers=headers, json=body, timeout=10)
            result = resp.json() if resp.text else {}
            logger.info(f"[QQ] Send API response: status={resp.status_code}, body={json.dumps(result, ensure_ascii=False)[:300]}")
            self._debug(f"[QQ] Send 返回: status={resp.status_code}, body={json.dumps(result, ensure_ascii=False)[:200]}")
            code = resp.status_code
            msg = result.get("message", "") or result.get("msg", "") or ""
            return {"code": 0 if code == 200 else code, "msg": msg,
                    "message_id": result.get("id", "")}
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._debug(f"[QQ] Send 异常: {type(e).__name__}: {e}")
            logger.error(f"[QQ] Send 异常: {tb}")
            return {"code": -1, "msg": str(e)}
