"""Send text and image messages through the official QQ Bot OpenAPI."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

QQTargetType = Literal["group", "c2c", "channel"]

_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
_API_BASE_URL = "https://api.sgroup.qq.com"


class QQBotError(RuntimeError):
    """Raised when the QQ Bot OpenAPI request fails."""


class QQBotClient:
    """QQ Bot client that reuses one access token for a batch send."""

    def __init__(
        self,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        """Resolve credentials without making a network request."""
        self.app_id = app_id or os.getenv("QQBOT_APPID", "")
        self.app_secret = app_secret or os.getenv("QQBOT_SECRET", "")
        if not self.app_id or not self.app_secret:
            raise ValueError("QQBOT_APPID and QQBOT_SECRET are required")
        self.timeout = timeout
        self._access_token: str | None = None

    def send_text(
        self,
        target_type: QQTargetType,
        target_id: str,
        content: str,
        *,
        msg_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a text message."""
        if not content:
            raise ValueError("content must not be empty")
        payload: dict[str, Any] = {"content": content}
        if target_type in {"group", "c2c"}:
            payload["msg_type"] = 0
        if msg_id is not None:
            payload["msg_id"] = msg_id
        return self._send_payload(target_type, target_id, payload)

    def send_image(
        self,
        target_type: QQTargetType,
        target_id: str,
        image_path: str | Path,
        *,
        content: str = "",
        msg_id: str | None = None,
    ) -> dict[str, Any]:
        """Upload a local PNG/JPEG and send it as a rich-media message."""
        if target_type == "channel":
            raise ValueError("local image upload supports only group and c2c targets")
        path = Path(image_path)
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            raise ValueError("image_path must be a PNG or JPEG file")
        image_bytes = path.read_bytes()
        if not image_bytes:
            raise ValueError("image_path must not be empty")

        upload = _post_json(
            _file_endpoint(target_type, target_id),
            {
                "file_type": 1,
                "file_data": base64.b64encode(image_bytes).decode("ascii"),
                "srv_send_msg": False,
            },
            headers=self._headers(),
            timeout=self.timeout,
        )
        file_info = upload.get("file_info")
        if not isinstance(file_info, str) or not file_info:
            raise QQBotError("QQ Bot media response did not contain file_info")

        payload: dict[str, Any] = {
            "content": content,
            "msg_type": 7,
            "media": {"file_info": file_info},
        }
        if msg_id is not None:
            payload["msg_id"] = msg_id
        return self._send_payload(target_type, target_id, payload)

    def _send_payload(
        self,
        target_type: QQTargetType,
        target_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not target_id:
            raise ValueError("target_id must not be empty")
        return _post_json(
            _message_endpoint(target_type, target_id),
            payload,
            headers=self._headers(),
            timeout=self.timeout,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"QQBot {self._token()}",
            "X-Union-Appid": self.app_id,
        }

    def _token(self) -> str:
        if self._access_token is not None:
            return self._access_token
        token_response = _post_json(
            _TOKEN_URL,
            {"appId": self.app_id, "clientSecret": self.app_secret},
            timeout=self.timeout,
        )
        access_token = token_response.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise QQBotError("QQ Bot token response did not contain access_token")
        self._access_token = access_token
        return access_token


def send_qq_bot_message(
    target_type: QQTargetType,
    target_id: str,
    content: str,
    *,
    msg_id: str | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Send a text message with the official QQ Bot OpenAPI.

    ``target_id`` must be a ``group_openid`` for ``group``, a user ``openid``
    for ``c2c``, or a ``channel_id`` for ``channel``. Credentials default to
    the ``QQBOT_APPID`` and ``QQBOT_SECRET`` environment variables.
    """
    return QQBotClient(
        app_id=app_id,
        app_secret=app_secret,
        timeout=timeout,
    ).send_text(
        target_type,
        target_id,
        content,
        msg_id=msg_id,
    )


def _message_endpoint(target_type: QQTargetType, target_id: str) -> str:
    escaped_id = quote(target_id, safe="")
    paths = {
        "group": f"/v2/groups/{escaped_id}/messages",
        "c2c": f"/v2/users/{escaped_id}/messages",
        "channel": f"/channels/{escaped_id}/messages",
    }
    try:
        return f"{_API_BASE_URL}{paths[target_type]}"
    except KeyError as error:
        raise ValueError(f"unsupported target_type: {target_type}") from error


def _file_endpoint(target_type: QQTargetType, target_id: str) -> str:
    escaped_id = quote(target_id, safe="")
    paths = {
        "group": f"/v2/groups/{escaped_id}/files",
        "c2c": f"/v2/users/{escaped_id}/files",
    }
    try:
        return f"{_API_BASE_URL}{paths[target_type]}"
    except KeyError as error:
        raise ValueError(f"unsupported media target_type: {target_type}") from error


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float,
) -> dict[str, Any]:
    request_headers = {"Content-Type": "application/json"}
    if headers is not None:
        request_headers.update(headers)
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise QQBotError(f"QQ Bot API returned HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise QQBotError(f"QQ Bot API request failed: {error.reason}") from error

    try:
        result = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise QQBotError("QQ Bot API returned invalid JSON") from error
    if not isinstance(result, dict):
        raise QQBotError("QQ Bot API returned a non-object response")
    return result
