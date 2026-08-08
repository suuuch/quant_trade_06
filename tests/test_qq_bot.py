"""Tests for the official QQ Bot OpenAPI helper."""

from __future__ import annotations

import json
from email.message import Message
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from quant_trade.qq_bot import (
    QQBotClient,
    QQBotError,
    send_qq_bot_message,
    send_qq_group_message,
)


def _response(payload: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
    return response


@patch("quant_trade.qq_bot.urlopen")
def test_send_group_message(mock_urlopen: MagicMock) -> None:
    mock_urlopen.side_effect = [
        _response({"access_token": "token", "expires_in": "7200"}),
        _response({"id": "message-id", "timestamp": "2026-08-04T12:00:00+08:00"}),
    ]

    result = send_qq_bot_message(
        "group",
        "group-openid",
        "交易信号已生成",
        msg_id="source-message-id",
        app_id="app-id",
        app_secret="app-secret",
    )

    assert result["id"] == "message-id"
    token_request = mock_urlopen.call_args_list[0].args[0]
    message_request = mock_urlopen.call_args_list[1].args[0]
    assert isinstance(token_request, Request)
    assert json.loads(cast(bytes, token_request.data)) == {
        "appId": "app-id",
        "clientSecret": "app-secret",
    }
    assert isinstance(message_request, Request)
    assert message_request.full_url.endswith("/v2/groups/group-openid/messages")
    assert message_request.get_header("Authorization") == "QQBot token"
    assert json.loads(cast(bytes, message_request.data)) == {
        "content": "交易信号已生成",
        "msg_type": 0,
        "msg_id": "source-message-id",
    }


@patch("quant_trade.qq_bot.urlopen")
def test_send_default_group_message(
    mock_urlopen: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QQBOT_APPID", "app-id")
    monkeypatch.setenv("QQBOT_SECRET", "app-secret")
    monkeypatch.setenv("QQBOT_GROUP_OPENID", "configured-group")
    mock_urlopen.side_effect = [
        _response({"access_token": "token"}),
        _response({"id": "message-id"}),
    ]

    result = send_qq_group_message("默认群消息")

    assert result["id"] == "message-id"
    message_request = mock_urlopen.call_args_list[1].args[0]
    assert message_request.full_url.endswith(
        "/v2/groups/configured-group/messages"
    )


@patch("quant_trade.qq_bot.urlopen")
def test_send_channel_message_omits_group_fields(mock_urlopen: MagicMock) -> None:
    mock_urlopen.side_effect = [
        _response({"access_token": "token"}),
        _response({"id": "message-id"}),
    ]

    send_qq_bot_message(
        "channel",
        "channel/id",
        "hello",
        app_id="app-id",
        app_secret="app-secret",
    )

    message_request = mock_urlopen.call_args_list[1].args[0]
    assert message_request.full_url.endswith("/channels/channel%2Fid/messages")
    assert json.loads(cast(bytes, message_request.data)) == {"content": "hello"}


def test_missing_credentials_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QQBOT_APPID", raising=False)
    monkeypatch.delenv("QQBOT_SECRET", raising=False)

    with pytest.raises(ValueError, match="QQBOT_APPID"):
        send_qq_bot_message("c2c", "openid", "hello")


@patch("quant_trade.qq_bot.urlopen")
def test_http_error_includes_qq_response(mock_urlopen: MagicMock) -> None:
    error = HTTPError("url", 401, "Unauthorized", Message(), None)
    error.read = MagicMock(return_value=b'{"message":"invalid app secret"}')
    mock_urlopen.side_effect = error

    with pytest.raises(QQBotError, match="invalid app secret"):
        send_qq_bot_message(
            "c2c",
            "openid",
            "hello",
            app_id="app-id",
            app_secret="bad-secret",
        )


@patch("quant_trade.qq_bot.urlopen")
def test_send_c2c_image_uploads_then_sends_media(
    mock_urlopen: MagicMock,
    tmp_path: Path,
) -> None:
    image = tmp_path / "signal.png"
    image.write_bytes(b"png-bytes")
    mock_urlopen.side_effect = [
        _response({"access_token": "token"}),
        _response({"file_info": "uploaded-file"}),
        _response({"id": "message-id"}),
    ]

    result = QQBotClient(
        app_id="app-id",
        app_secret="app-secret",
    ).send_image("c2c", "user-openid", image, content="signal")

    assert result["id"] == "message-id"
    upload_request = mock_urlopen.call_args_list[1].args[0]
    message_request = mock_urlopen.call_args_list[2].args[0]
    assert upload_request.full_url.endswith("/v2/users/user-openid/files")
    assert json.loads(cast(bytes, upload_request.data)) == {
        "file_type": 1,
        "file_data": "cG5nLWJ5dGVz",
        "srv_send_msg": False,
    }
    assert json.loads(cast(bytes, message_request.data)) == {
        "content": "signal",
        "msg_type": 7,
        "media": {"file_info": "uploaded-file"},
    }
