"""Listen for QQ Bot messages and print their OpenIDs."""

from __future__ import annotations

import json
import os

import botpy
from botpy.message import C2CMessage, GroupMessage
from dotenv import load_dotenv


class OpenIdListener(botpy.Client):
    """Print identifiers from incoming QQ C2C and group messages."""

    async def on_ready(self) -> None:
        """Report that the WebSocket connection is ready."""
        print("QQ WebSocket 已连接，可以发送消息。", flush=True)

    async def on_c2c_message_create(self, message: C2CMessage) -> None:
        """Print identifiers from an incoming C2C message."""
        _print_event(
            {
                "event": "C2C_MESSAGE_CREATE",
                "user_openid": message.author.user_openid,
                "msg_id": message.id,
                "content": message.content,
            }
        )

    async def on_group_at_message_create(self, message: GroupMessage) -> None:
        """Print identifiers from an incoming group mention message."""
        _print_event(
            {
                "event": "GROUP_AT_MESSAGE_CREATE",
                "group_openid": message.group_openid,
                "member_openid": message.author.member_openid,
                "msg_id": message.id,
                "content": message.content,
            }
        )


def _print_event(payload: dict[str, str | None]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main() -> None:
    """Load credentials and run the QQ Bot WebSocket listener."""
    load_dotenv()
    app_id = os.getenv("QQBOT_APPID")
    app_secret = os.getenv("QQBOT_SECRET")
    if not app_id or not app_secret:
        raise RuntimeError("QQBOT_APPID and QQBOT_SECRET are required")

    intents = botpy.Intents(public_messages=True)
    client = OpenIdListener(intents=intents)
    client.run(appid=app_id, secret=app_secret)


if __name__ == "__main__":
    main()
