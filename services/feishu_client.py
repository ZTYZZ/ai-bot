import json
import logging
from datetime import datetime, timezone, timedelta

from lark_oapi import Client

from config import FEISHU_APP_ID, FEISHU_APP_SECRET

logger = logging.getLogger(__name__)


class FeishuClient:
    """lark-oapi SDK 封装，统一管理所有飞书 API 调用。"""

    def __init__(self):
        self._client = (
            Client.builder()
            .app_id(FEISHU_APP_ID)
            .app_secret(FEISHU_APP_SECRET)
            .build()
        )

    # ============================================================
    # 消息发送
    # ============================================================

    def send_text_message(self, receive_id: str, receive_id_type: str, content: str) -> dict:
        """发送文本消息，返回 {code, msg, message_id}。"""
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        body = (
            CreateMessageRequestBody.builder()
            .receive_id(receive_id)
            .msg_type("text")
            .content(json.dumps({"text": content}, ensure_ascii=False))
            .build()
        )
        req = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(body)
            .build()
        )

        try:
            resp = self._client.im.v1.message.create(req)
            return {
                "code": resp.code,
                "msg": resp.msg,
                "message_id": resp.data.message_id if resp.data else None,
            }
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            return {"code": -1, "msg": str(e), "message_id": None}

    # ============================================================
    # 用户 / 通讯录
    # ============================================================

    def get_user_info(self, open_id: str) -> dict:
        """查询用户信息，返回 {code, msg, user}。"""
        from lark_oapi.api.contact.v3 import GetUserRequest

        req = (
            GetUserRequest.builder()
            .user_id_type("open_id")
            .user_id(open_id)
            .build()
        )

        try:
            resp = self._client.contact.v3.user.get(req)
            if resp.code == 0 and resp.data and resp.data.user:
                u = resp.data.user
                return {
                    "code": 0,
                    "msg": "success",
                    "user": {
                        "open_id": u.open_id,
                        "name": u.name,
                        "en_name": getattr(u, "en_name", ""),
                        "email": getattr(u, "email", ""),
                        "mobile": getattr(u, "mobile", ""),
                        "employee_no": getattr(u, "employee_no", ""),
                        "department_ids": getattr(u, "department_ids", []),
                        "job_title": getattr(u, "job_title", ""),
                        "avatar_url": getattr(u, "avatar", {}).get("avatar_240", "") if hasattr(u, "avatar") and u.avatar else "",
                    },
                }
            return {"code": resp.code, "msg": resp.msg, "user": None}
        except Exception as e:
            logger.error(f"查询用户失败: {e}")
            return {"code": -1, "msg": str(e), "user": None}

    def list_tenant_users(self, page_size: int = 50, page_token: str = "") -> dict:
        """列出租户下所有用户，返回 {code, msg, users, has_more, page_token}。"""
        from lark_oapi.api.contact.v3 import ListUserRequest

        req = (
            ListUserRequest.builder()
            .user_id_type("open_id")
            .page_size(page_size)
            .page_token(page_token or "")
            .build()
        )

        try:
            resp = self._client.contact.v3.user.list(req)
            if resp.code == 0 and resp.data:
                items = resp.data.items or []
                return {
                    "code": 0,
                    "msg": "success",
                    "users": [
                        {
                            "open_id": u.open_id,
                            "name": u.name,
                            "en_name": getattr(u, "en_name", ""),
                            "email": getattr(u, "email", ""),
                            "mobile": getattr(u, "mobile", ""),
                            "department_ids": getattr(u, "department_ids", []),
                            "job_title": getattr(u, "job_title", ""),
                        }
                        for u in items
                    ],
                    "has_more": resp.data.has_more,
                    "page_token": resp.data.page_token or "",
                }
            return {"code": resp.code, "msg": resp.msg, "users": [], "has_more": False, "page_token": ""}
        except Exception as e:
            logger.error(f"列出用户失败: {e}")
            return {"code": -1, "msg": str(e), "users": [], "has_more": False, "page_token": ""}

    # ============================================================
    # 日历
    # ============================================================

    def create_calendar_event(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        description: str = "",
        calendar_id: str = "primary",
        timezone: str = "Asia/Shanghai",
    ) -> dict:
        """创建日历事件，返回 {code, msg, event_id}。

        start_time / end_time: ISO 8601 格式，如 '2026-04-28T14:00:00'
        """
        from lark_oapi.api.calendar.v4 import (
            CreateCalendarEventRequest,
            CalendarEvent,
            TimeInfo,
        )

        tz = timezone(timedelta(hours=8))  # Asia/Shanghai

        # 解析 ISO 时间字符串 → Unix 时间戳（毫秒）
        try:
            st_dt = datetime.fromisoformat(start_time)
            et_dt = datetime.fromisoformat(end_time)
            st_ts = str(int(st_dt.timestamp() * 1000))
            et_ts = str(int(et_dt.timestamp() * 1000)) if end_time else ""
        except ValueError:
            # 如果解析失败，直接传递原字符串（可能已经是时间戳）
            st_ts = start_time
            et_ts = end_time

        start_ti = TimeInfo.builder().timestamp(st_ts).timezone(timezone).build()
        end_ti = TimeInfo.builder().timestamp(et_ts).timezone(timezone).build()

        body = (
            CalendarEvent.builder()
            .summary(summary)
            .start_time(start_ti)
            .end_time(end_ti)
        )
        if description:
            body = body.description(description)

        req = (
            CreateCalendarEventRequest.builder()
            .calendar_id(calendar_id)
            .request_body(body.build())
            .build()
        )

        try:
            resp = self._client.calendar.v4.calendar_event.create(req)
            return {
                "code": resp.code,
                "msg": resp.msg,
                "event_id": resp.data.event_id if resp.data else None,
            }
        except Exception as e:
            logger.error(f"创建日历事件失败: {e}")
            return {"code": -1, "msg": str(e), "event_id": None}

    def list_calendar_events(
        self,
        calendar_id: str = "primary",
        start_time: str = "",
        end_time: str = "",
        page_size: int = 20,
    ) -> dict:
        """列出日历事件，返回 {code, msg, events}。"""
        from lark_oapi.api.calendar.v4 import ListCalendarEventRequest

        req_builder = (
            ListCalendarEventRequest.builder()
            .calendar_id(calendar_id)
            .page_size(page_size)
        )
        if start_time:
            req_builder = req_builder.start_time(start_time)
        if end_time:
            req_builder = req_builder.end_time(end_time)

        try:
            resp = self._client.calendar.v4.calendar_event.list(req_builder.build())
            if resp.code == 0 and resp.data:
                items = resp.data.items or []
                return {
                    "code": 0,
                    "msg": "success",
                    "events": [
                        {
                            "event_id": e.event_id,
                            "summary": e.summary,
                            "description": getattr(e, "description", ""),
                            "start_time": getattr(e, "start_time", {}),
                            "end_time": getattr(e, "end_time", {}),
                            "status": getattr(e, "status", ""),
                        }
                        for e in items
                    ],
                }
            return {"code": resp.code, "msg": resp.msg, "events": []}
        except Exception as e:
            logger.error(f"查询日历事件失败: {e}")
            return {"code": -1, "msg": str(e), "events": []}

    # ============================================================
    # 任务
    # ============================================================

    def _get_tenant_token(self) -> str:
        """获取 tenant_access_token（直接 HTTP，绕过 SDK）"""
        import requests as _requests
        try:
            resp = _requests.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
                timeout=10,
            )
            data = resp.json()
            token = data.get("tenant_access_token", "")
            if not token:
                logger.error(f"[Task] 获取 tenant token 失败: {data}")
            return token
        except Exception as e:
            logger.error(f"[Task] 获取 tenant token 异常: {e}")
            return ""

    def create_task(
        self,
        summary: str,
        description: str = "",
        due_date: str = "",
        member_open_ids: list = None,
    ) -> dict:
        """创建任务（直接 HTTP 调用），返回 {code, msg, task_id}。

        due_date: 格式 '2026-04-28' 或 ISO 8601
        """
        import requests as _requests

        # 构建请求体 — 只包含非空字段
        body = {"summary": summary}

        if description:
            desc = description.strip()
            if len(desc) > 3000:
                desc = desc[:3000]
            if desc:
                body["description"] = desc

        if due_date:
            try:
                dt = None
                for fmt in [None, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                           "%Y年%m月%d日", "%Y/%m/%d", "%Y-%m-%dT%H:%M", "%Y-%m-%d"]:
                    try:
                        if fmt:
                            dt = datetime.strptime(due_date.strip(), fmt)
                        else:
                            dt = datetime.fromisoformat(due_date.strip())
                        break
                    except ValueError:
                        continue
                if dt:
                    body["due"] = {
                        "timestamp": str(int(dt.timestamp() * 1000)),
                    }
            except Exception:
                pass

        if member_open_ids:
            body["members"] = [
                {"id": oid, "type": "user", "role": "assignee"}
                for oid in member_open_ids if oid
            ]

        token = self._get_tenant_token()
        if not token:
            return {"code": -1, "msg": "无法获取飞书 tenant_access_token", "task_id": None}

        url = "https://open.feishu.cn/open-apis/task/v2/tasks?user_id_type=open_id"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            body_json = json.dumps(body, ensure_ascii=False)
            logger.info(f"[Task] 创建请求: url={url}, body={body_json[:500]}")
            resp = _requests.post(url, headers=headers, data=body_json.encode("utf-8"), timeout=15)
            resp_data = resp.json()
            logger.info(f"[Task] API 返回: status={resp.status_code}, body={json.dumps(resp_data, ensure_ascii=False)[:500]}")

            code = resp_data.get("code", -1)
            msg = resp_data.get("msg", "")
            if code == 0:
                task = resp_data.get("data", {}).get("task", {})
                task_id = task.get("id", "")
                return {"code": 0, "msg": "success", "task_id": task_id}
            else:
                # 提取字段校验详情
                violations = ""
                field_violations = resp_data.get("data", {}).get("field_violations", []) or []
                if field_violations:
                    parts = [f"{v.get('field', '?')}: {v.get('description', '?')}" for v in field_violations]
                    violations = " | ".join(parts)
                error_detail = f"code={code}, msg={msg}" + (f" [{violations}]" if violations else "")
                logger.error(f"[Task] 创建失败: {error_detail}")
                return {"code": code, "msg": error_detail, "task_id": None}
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"[Task] 创建异常: {tb}")
            return {"code": -1, "msg": f"{type(e).__name__}: {e}", "task_id": None}

    def list_tasks(self, page_size: int = 20, page_token: str = "", completed: bool = None) -> dict:
        """列出任务（直接 HTTP 调用，与 create_task 保持一致），返回 {code, msg, tasks, has_more, page_token}。"""
        import requests as _requests
        from urllib.parse import urlencode

        token = self._get_tenant_token()
        if not token:
            return {"code": -1, "msg": "无法获取飞书 tenant_access_token", "tasks": [], "has_more": False, "page_token": ""}

        params = {"user_id_type": "open_id", "page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        if completed is not None:
            params["completed"] = str(completed).lower()

        url = f"https://open.feishu.cn/open-apis/task/v2/tasks?{urlencode(params)}"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            resp = _requests.get(url, headers=headers, timeout=15)
            resp_data = resp.json()
            logger.info(f"[Task] List 返回: status={resp.status_code}, code={resp_data.get('code')}")

            code = resp_data.get("code", -1)
            if code == 0:
                data = resp_data.get("data", {})
                items = data.get("items", []) or []
                return {
                    "code": 0,
                    "msg": "success",
                    "tasks": [
                        {
                            "id": t.get("id", ""),
                            "summary": t.get("summary", ""),
                            "description": t.get("description", ""),
                            "completed": t.get("completed", False),
                            "created_at": t.get("created_at", ""),
                            "due": t.get("due", None),
                        }
                        for t in items
                    ],
                    "has_more": data.get("has_more", False),
                    "page_token": data.get("page_token", ""),
                }
            return {"code": code, "msg": resp_data.get("msg", ""), "tasks": [], "has_more": False, "page_token": ""}
        except Exception as e:
            logger.error(f"查询任务失败: {e}")
            return {"code": -1, "msg": str(e), "tasks": [], "has_more": False, "page_token": ""}

    # ============================================================
    # 文档
    # ============================================================

    def create_doc(self, title: str, folder_token: str = "") -> dict:
        """创建飞书文档，返回 {code, msg, document_id, url}。"""
        from lark_oapi.api.docx.v1 import CreateDocumentRequest, CreateDocumentRequestBody

        body_builder = CreateDocumentRequestBody.builder().title(title)
        if folder_token:
            body_builder = body_builder.folder_token(folder_token)

        req = (
            CreateDocumentRequest.builder()
            .request_body(body_builder.build())
            .build()
        )

        try:
            resp = self._client.docx.v1.document.create(req)
            if resp.code == 0 and resp.data and resp.data.document:
                doc = resp.data.document
                return {
                    "code": 0,
                    "msg": "success",
                    "document_id": doc.document_id,
                    "url": f"https://bytedance.feishu.cn/docx/{doc.document_id}",
                }
            return {"code": resp.code, "msg": resp.msg, "document_id": None, "url": ""}
        except Exception as e:
            logger.error(f"创建文档失败: {e}")
            return {"code": -1, "msg": str(e), "document_id": None, "url": ""}

    # ============================================================
    # 表格
    # ============================================================

    def query_sheet_data(self, spreadsheet_token: str) -> dict:
        """查询电子表格的元数据（所有工作表信息），返回 {code, msg, sheets}。"""
        from lark_oapi.api.sheets.v3 import QuerySpreadsheetSheetRequest

        req = (
            QuerySpreadsheetSheetRequest.builder()
            .spreadsheet_token(spreadsheet_token)
            .build()
        )

        try:
            resp = self._client.sheets.v3.spreadsheet_sheet.query(req)
            if resp.code == 0 and resp.data:
                sheets = resp.data.sheets or []
                return {
                    "code": 0,
                    "msg": "success",
                    "sheets": [
                        {
                            "sheet_id": s.sheet_id,
                            "title": getattr(s, "title", ""),
                            "row_count": getattr(s, "grid_properties", {}).get("row_count", 0) if hasattr(s, "grid_properties") else 0,
                            "column_count": getattr(s, "grid_properties", {}).get("column_count", 0) if hasattr(s, "grid_properties") else 0,
                        }
                        for s in sheets
                    ],
                }
            return {"code": resp.code, "msg": resp.msg, "sheets": []}
        except Exception as e:
            logger.error(f"查询表格失败: {e}")
            return {"code": -1, "msg": str(e), "sheets": []}

    # ============================================================
    # 搜索
    # ============================================================

    def search_messages(self, query: str, page_size: int = 10) -> dict:
        """搜索消息，返回 {code, msg, items, has_more, page_token}。"""
        from lark_oapi.api.search.v2 import (
            CreateMessageRequest as SearchMessageRequest,
            CreateMessageRequestBody as SearchMessageRequestBody,
        )

        body = SearchMessageRequestBody.builder().query(query).build()
        req = (
            SearchMessageRequest.builder()
            .user_id_type("open_id")
            .page_size(page_size)
            .request_body(body)
            .build()
        )

        try:
            resp = self._client.search.v2.message.search(req)
            if resp.code == 0 and resp.data:
                items = resp.data.items or []
                return {
                    "code": 0,
                    "msg": "success",
                    "items": [
                        {
                            "message_id": getattr(i, "message_id", ""),
                            "content": getattr(i, "content", ""),
                            "sender_name": getattr(i, "sender_name", ""),
                            "chat_name": getattr(i, "chat_name", ""),
                            "send_time": getattr(i, "send_time", ""),
                        }
                        for i in items
                    ],
                    "has_more": resp.data.has_more,
                    "page_token": resp.data.page_token or "",
                }
            return {"code": resp.code, "msg": resp.msg, "items": [], "has_more": False, "page_token": ""}
        except Exception as e:
            logger.error(f"搜索消息失败: {e}")
            return {"code": -1, "msg": str(e), "items": [], "has_more": False, "page_token": ""}
