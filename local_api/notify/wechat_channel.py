"""WeChat reminder notification channel with real webhook delivery support."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from typing import Optional

from .base import NotifyChannel, NotifyResult

_ENV_ENABLED = "WECHAT_REMINDER_ENABLED"
_ENV_APP_ID = "WECHAT_APP_ID"
_ENV_APP_SECRET = "WECHAT_APP_SECRET"
_ENV_WEBHOOK_URL = "WECHAT_REMINDER_WEBHOOK_URL"
_ENV_LEGACY_WEBHOOK_URL = "WECHAT_WEBHOOK_URL"


class WeChatNotifyChannel(NotifyChannel):
    """WeChat reminder notification channel.

    Real mode sends an HTTP POST to a configured WeChat-compatible webhook.
    The webhook can be Enterprise WeChat robot, Server酱, or a user-owned
    gateway that returns JSON or text. Dry-run and test_mode remain explicit
    non-real modes for tests only.
    """

    MODE_DRY_RUN = "dry_run"
    MODE_TEST = "test_mode"
    MODE_REAL = "real"

    _VALID_MODES = frozenset({MODE_DRY_RUN, MODE_TEST, MODE_REAL})

    def __init__(self, mode: str = MODE_DRY_RUN):
        if mode not in self._VALID_MODES:
            raise ValueError(
                f"WeChatNotifyChannel mode must be one of "
                f"{sorted(self._VALID_MODES)}, got: {mode}"
            )
        self._mode = mode

    @property
    def channel_name(self) -> str:
        return "wechat"

    def send_reminder(self, task_id: str, task_data: dict) -> NotifyResult:
        if self._mode == self.MODE_DRY_RUN:
            return NotifyResult(success=True, mode=self._mode, channel="wechat", task_id=task_id, status="skipped")

        if self._mode == self.MODE_TEST:
            return NotifyResult(success=True, mode=self._mode, channel="wechat", task_id=task_id, status="simulated")

        assert self._mode == self.MODE_REAL
        if not self._is_platform_configured():
            return NotifyResult(
                success=False,
                mode=self._mode,
                channel="wechat",
                task_id=task_id,
                status="config_error",
                error_code="platform_not_configured",
                error_message=f"Set {_ENV_ENABLED}=true and {_ENV_WEBHOOK_URL} for real WeChat delivery.",
            )

        webhook_url = self._webhook_url()
        if not webhook_url:
            return NotifyResult(
                success=False,
                mode=self._mode,
                channel="wechat",
                task_id=task_id,
                status="config_error",
                error_code="missing_webhook_url",
                error_message=f"Set {_ENV_WEBHOOK_URL} or {_ENV_LEGACY_WEBHOOK_URL}.",
            )

        return self._send_webhook(webhook_url, task_id, task_data)

    @staticmethod
    def _is_platform_configured() -> bool:
        return os.environ.get(_ENV_ENABLED, "").strip().lower() in {"true", "1", "yes"}

    @staticmethod
    def _has_credentials() -> bool:
        return bool(WeChatNotifyChannel._webhook_url())

    @staticmethod
    def _webhook_url() -> str:
        return (os.environ.get(_ENV_WEBHOOK_URL) or os.environ.get(_ENV_LEGACY_WEBHOOK_URL) or "").strip()

    def _send_webhook(self, webhook_url: str, task_id: str, task_data: dict) -> NotifyResult:
        payload = self._build_payload(task_id, task_data)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_id = hashlib.sha256(body).hexdigest()[:24]
        req = urllib.request.Request(
            webhook_url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "LifeSync/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                response_body = response.read().decode("utf-8", errors="replace")
                provider_response = self._parse_response(response_body)
                success = 200 <= response.status < 300 and self._provider_success(provider_response)
                message_id = self._message_id(provider_response, request_id)
                return NotifyResult(
                    success=success,
                    mode=self._mode,
                    channel="wechat",
                    task_id=task_id,
                    status="sent" if success else "failed",
                    error_code=None if success else "provider_rejected",
                    error_message=None if success else response_body[:500],
                    message_id=message_id,
                    request_id=request_id,
                    provider_response=provider_response,
                )
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            return NotifyResult(
                success=False,
                mode=self._mode,
                channel="wechat",
                task_id=task_id,
                status="failed",
                error_code=f"http_{exc.code}",
                error_message=error_body[:500] or str(exc),
                request_id=request_id,
                provider_response={"http_status": exc.code, "body": error_body[:500]},
            )
        except Exception as exc:
            return NotifyResult(
                success=False,
                mode=self._mode,
                channel="wechat",
                task_id=task_id,
                status="failed",
                error_code="send_error",
                error_message=str(exc),
                request_id=request_id,
            )

    @staticmethod
    def _build_payload(task_id: str, task_data: dict) -> dict:
        title = task_data.get("title") or task_id
        start = task_data.get("start_time") or task_data.get("due_time") or "未设置时间"
        location = task_data.get("location") or "未设置地点"
        content = f"LifeSync 日程提醒\n标题：{title}\n时间：{start}\n地点：{location}\n任务ID：{task_id}"
        return {
            "msgtype": "text",
            "text": {"content": content},
            "task_id": task_id,
            "title": title,
            "start_time": start,
            "location": location,
        }

    @staticmethod
    def _parse_response(response_body: str) -> dict:
        try:
            parsed = json.loads(response_body) if response_body else {}
            return parsed if isinstance(parsed, dict) else {"body": parsed}
        except json.JSONDecodeError:
            return {"body": response_body}

    @staticmethod
    def _provider_success(provider_response: dict) -> bool:
        if not provider_response:
            return True
        if "errcode" in provider_response:
            return provider_response.get("errcode") == 0
        if "code" in provider_response and str(provider_response.get("code")) not in {"0", "200"}:
            return False
        if "success" in provider_response:
            return bool(provider_response.get("success"))
        return True

    @staticmethod
    def _message_id(provider_response: dict, fallback: str) -> str:
        for key in ("message_id", "msgid", "id", "request_id"):
            if provider_response.get(key):
                return str(provider_response[key])
        return fallback
