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
    """Intercept send_message calls and capture (chat_id, text) tuples."""
    messages = []

    def fake_send(chat_id: int, text: str, **kwargs):
        item = {"chat_id": chat_id, "text": text}
        if kwargs:
            item.update(kwargs)
        messages.append(item)

    with patch("api.webhook.send_message", side_effect=fake_send):
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