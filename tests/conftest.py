import json
import threading
from http.server import HTTPServer
from unittest.mock import patch

import pytest
from dotenv import load_dotenv

load_dotenv()

# Import after load_dotenv so env vars are available at module level
from api.webhook import handler


@pytest.fixture(scope="session")
def webhook_server():
    """Start a local HTTP server running the webhook handler."""
    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture()
def captured_messages():
    """
    Intercept send_message and send_message_with_buttons calls.
    Provides an in-memory Redis store so tests don't need a real Redis connection.
    Patches answer_callback_query and clear_message_buttons as no-ops.
    """
    messages = []
    _redis_store: dict = {}
    _preview_msg: dict = {}

    def fake_send(chat_id: int, text: str, **kwargs):
        item = {"chat_id": chat_id, "text": text}
        if kwargs:
            item.update(kwargs)
        messages.append(item)

    def fake_send_with_buttons(chat_id: int, text: str) -> int:
        messages.append({"chat_id": chat_id, "text": text, "has_buttons": True})
        return 999  # fake message_id

    with (
        patch("api.webhook.send_message", side_effect=fake_send),
        patch("api.webhook.send_message_with_buttons", side_effect=fake_send_with_buttons),
        patch("api.webhook.redis_set_pending", side_effect=lambda cid, rows: _redis_store.update({cid: rows})),
        patch("api.webhook.redis_get_pending", side_effect=lambda cid: _redis_store.get(cid)),
        patch("api.webhook.redis_del_pending", side_effect=lambda cid: _redis_store.pop(cid, None)),
        patch("api.webhook.redis_set_preview_msg", side_effect=lambda cid, mid: _preview_msg.update({cid: mid})),
        patch("api.webhook.redis_get_preview_msg", side_effect=lambda cid: _preview_msg.get(cid)),
        patch("api.webhook.clear_message_buttons"),
        patch("api.webhook.answer_callback_query"),
    ):
        yield messages


def make_telegram_update(text: str, chat_id: int = 123456789, username: str = "testuser"):
    """Build a minimal Telegram Update payload."""
    return {
        "update_id": 100000001,
        "message": {
            "message_id": 1,
            "from": {
                "id": chat_id,
                "is_bot": False,
                "first_name": "Test",
                "username": username,
            },
            "chat": {"id": chat_id, "type": "private"},
            "date": 1713168000,
            "text": text,
        },
    }