import json
import re

import httpx

from tests.conftest import make_telegram_update


def make_confirm_callback(chat_id: int = 123456789, message_id: int = 999):
    """Build a minimal Telegram callback_query payload for ✅ Simpan."""
    return {
        "update_id": 100000002,
        "callback_query": {
            "id": "cb_test_123",
            "from": {"id": chat_id, "is_bot": False, "first_name": "Test"},
            "message": {
                "message_id": message_id,
                "chat": {"id": chat_id, "type": "private"},
            },
            "data": "cb_confirm",
        },
    }


# Valid expense inputs (Bahasa, English, slang, mixed)
EXPENSE_INPUTS = [
    {
        "text": "makan bakso 2 mangkok 24000 cash",
        "expect_category": "FnB",
        "expect_amount": 24000,
        "expect_source": "Cash",
    },
    {
        "text": "grab ke kantor 35rb gopay",
        "expect_category": "Transport",
        "expect_amount": 35000,
        "expect_source": "GoPay",
    },
    {
        "text": "beli detergen 45 ribu BCA",
        "expect_category": "Supplies",
        "expect_amount": 45000,
        "expect_source": "BCA",
    },
]


class TestWebhookStartCommand:
    def test_start_returns_greeting(
        self, webhook_server: str, captured_messages
    ):
        payload = make_telegram_update("/start")
        response = httpx.post(
            f"{webhook_server}/api/webhook",
            json=payload,
        )
        assert response.status_code == 200
        assert len(captured_messages) == 1
        assert "Kirim pesan expense" in captured_messages[0]["text"]


class TestWebhookExpenseParsing:
    """Full integration: real AI parsing (Groq/Gemini) — verifies preview is shown."""

    def test_expense_bahasa(
        self, webhook_server: str, captured_messages
    ):
        payload = make_telegram_update("makan bakso 2 mangkok 24000 cash")
        response = httpx.post(
            f"{webhook_server}/api/webhook",
            json=payload,
        )
        assert response.status_code == 200
        assert len(captured_messages) == 1

        reply = captured_messages[0]["text"]
        assert "📋" in reply or "Review" in reply
        assert "24,000" in reply

    def test_expense_with_slang(
        self, webhook_server: str, captured_messages
    ):
        payload = make_telegram_update("grab ke kantor 35rb gopay")
        response = httpx.post(
            f"{webhook_server}/api/webhook",
            json=payload,
        )
        assert response.status_code == 200
        assert len(captured_messages) == 1

        reply = captured_messages[0]["text"]
        assert "📋" in reply or "Review" in reply
        assert "35,000" in reply

    def test_expense_english(
        self, webhook_server: str, captured_messages
    ):
        payload = make_telegram_update("lunch at restaurant 120000 BCA")
        response = httpx.post(
            f"{webhook_server}/api/webhook",
            json=payload,
        )
        assert response.status_code == 200
        assert len(captured_messages) == 1

        reply = captured_messages[0]["text"]
        assert "📋" in reply or "Review" in reply
        assert "120,000" in reply

    def test_reply_contains_preview_format(
        self, webhook_server: str, captured_messages
    ):
        payload = make_telegram_update("beli detergen 45 ribu BCA")
        response = httpx.post(
            f"{webhook_server}/api/webhook",
            json=payload,
        )
        assert response.status_code == 200
        reply = captured_messages[0]["text"]

        # Preview format: "1. {desc} — Rp{amount} | {cat} | {type} | {src} | {date}"
        assert "1." in reply
        assert "Rp" in reply
        assert "45,000" in reply
        assert captured_messages[0].get("has_buttons")

    def test_date_in_preview(
        self, webhook_server: str, captured_messages
    ):
        payload = make_telegram_update("kopi starbucks 65rb cash kemarin")
        response = httpx.post(
            f"{webhook_server}/api/webhook",
            json=payload,
        )
        assert response.status_code == 200
        reply = captured_messages[0]["text"]

        # Date should be YYYY-MM-DD format somewhere in the preview
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", reply)
        assert date_match, f"No valid date found in reply: {reply}"


class TestWebhookInvalidInput:
    def test_nonsense_input(
        self, webhook_server: str, captured_messages
    ):
        payload = make_telegram_update("hello how are you")
        response = httpx.post(
            f"{webhook_server}/api/webhook",
            json=payload,
        )
        assert response.status_code == 200
        # Bot always sends exactly one message (preview or error, depending on AI response)
        assert len(captured_messages) == 1

    def test_empty_message_body(
        self, webhook_server: str, captured_messages
    ):
        payload = {"update_id": 100000002}  # no message field
        response = httpx.post(
            f"{webhook_server}/api/webhook",
            json=payload,
        )
        assert response.status_code == 200
        assert len(captured_messages) == 0


class TestWebhookGoogleSheets:
    """Verify rows are written to Google Sheets after confirm callback."""

    def test_expense_row_appended_after_confirm(
        self, webhook_server: str, captured_messages
    ):
        from api.webhook import get_worksheet

        ws = get_worksheet()
        rows_before = len(ws.get_all_values())

        # Step 1: Send expense → parses → stages it (in-memory Redis via fixture)
        payload = make_telegram_update("beli air mineral 5000 cash")
        r1 = httpx.post(f"{webhook_server}/api/webhook", json=payload)
        assert r1.status_code == 200
        assert len(captured_messages) == 1
        assert "📋" in captured_messages[0]["text"] or "Review" in captured_messages[0]["text"]

        # Step 2: Simulate ✅ Simpan callback
        confirm = make_confirm_callback()
        r2 = httpx.post(f"{webhook_server}/api/webhook", json=confirm)
        assert r2.status_code == 200

        # Confirm message is the second message (send_message after write)
        assert len(captured_messages) == 2
        assert "tersimpan" in captured_messages[1]["text"]

        # Verify new row was written
        rows_after = len(ws.get_all_values())
        assert rows_after == rows_before + 1

        last_row = ws.get_all_values()[-1]
        # Amount column may be formatted (e.g. "Rp 5.000"), just check digits present
        assert "5000" in last_row[6] or "5.000" in last_row[6] or "5,000" in last_row[6]

    def test_batch_rows_appended_after_confirm(
        self, webhook_server: str, captured_messages
    ):
        from api.webhook import get_worksheet

        ws = get_worksheet()
        rows_before = len(ws.get_all_values())

        payload = make_telegram_update(
            "makan bakso 24000 cash\ngrab ke kantor 35000 gopay"
        )
        r1 = httpx.post(f"{webhook_server}/api/webhook", json=payload)
        assert r1.status_code == 200

        confirm = make_confirm_callback()
        r2 = httpx.post(f"{webhook_server}/api/webhook", json=confirm)
        assert r2.status_code == 200

        rows_after = len(ws.get_all_values())
        assert rows_after == rows_before + 2


class TestWebhookBatchParsing:
    """Integration tests for multi-line (batch) expense input."""

    def test_batch_all_valid(self, webhook_server: str, captured_messages):
        payload = make_telegram_update(
            "makan bakso 24000 cash\ngrab ke kantor 35000 gopay"
        )
        response = httpx.post(f"{webhook_server}/api/webhook", json=payload)
        assert response.status_code == 200
        assert len(captured_messages) == 1
        reply = captured_messages[0]["text"]
        assert "📋" in reply or "Review" in reply
        # Both items shown as numbered list
        assert "1." in reply
        assert "2." in reply

    def test_batch_partial_failure(self, webhook_server: str, captured_messages):
        """One valid expense + one nonsense → valid staged, failure reported in preview."""
        payload = make_telegram_update(
            "makan siang nasi goreng 50000 gopay\nhello how are you"
        )
        response = httpx.post(f"{webhook_server}/api/webhook", json=payload)
        assert response.status_code == 200
        assert len(captured_messages) == 1
        reply = captured_messages[0]["text"]
        assert "📋" in reply or "Review" in reply
        assert "❌" in reply  # failure section

    def test_batch_blank_lines_ignored(self, webhook_server: str, captured_messages):
        """Blank lines between expenses are silently ignored."""
        payload = make_telegram_update(
            "makan bakso 24000 cash\n\n\ngrab ke kantor 35000 gopay"
        )
        response = httpx.post(f"{webhook_server}/api/webhook", json=payload)
        assert response.status_code == 200
        reply = captured_messages[0]["text"]
        assert "1." in reply
        assert "2." in reply

    def test_batch_over_limit(self, webhook_server: str, captured_messages):
        """Message exceeding MAX_BATCH_LINES returns an error without staging."""
        from api.webhook import MAX_BATCH_LINES

        lines = "\n".join(
            [f"makan {i * 1000} cash" for i in range(1, MAX_BATCH_LINES + 2)]
        )
        payload = make_telegram_update(lines)
        response = httpx.post(f"{webhook_server}/api/webhook", json=payload)
        assert response.status_code == 200
        assert len(captured_messages) == 1
        reply = captured_messages[0]["text"]
        assert "Terlalu banyak" in reply or "maks" in reply

    def test_single_line_shows_preview(
        self, webhook_server: str, captured_messages
    ):
        """Single-line input shows the pre-commit preview with inline buttons."""
        payload = make_telegram_update("beli air mineral 5000 cash")
        response = httpx.post(f"{webhook_server}/api/webhook", json=payload)
        assert response.status_code == 200
        assert len(captured_messages) == 1
        reply = captured_messages[0]["text"]
        assert "📋" in reply or "Review" in reply
        assert "1." in reply
        assert "Rp5,000" in reply
        assert captured_messages[0].get("has_buttons")