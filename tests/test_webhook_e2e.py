import json
import re

import httpx

from tests.conftest import make_telegram_update

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
    """Full integration: real AI parsing (Groq/Gemini) + real Google Sheets write."""

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
        assert "✅" in reply or "Tercatat" in reply
        assert "24,000" in reply or "24000" in reply

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
        assert "✅" in reply or "Tercatat" in reply
        assert "35,000" in reply or "35000" in reply

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
        assert "✅" in reply or "Tercatat" in reply
        assert "120,000" in reply or "120000" in reply

    def test_reply_contains_all_fields(
        self, webhook_server: str, captured_messages
    ):
        payload = make_telegram_update("beli detergen 45 ribu BCA")
        response = httpx.post(
            f"{webhook_server}/api/webhook",
            json=payload,
        )
        assert response.status_code == 200
        reply = captured_messages[0]["text"]

        assert "Tanggal:" in reply
        assert "Deskripsi:" in reply
        assert "Kategori:" in reply
        assert "Type:" in reply
        assert "Source:" in reply
        assert "Jumlah:" in reply

    def test_date_format_in_reply(
        self, webhook_server: str, captured_messages
    ):
        payload = make_telegram_update("kopi starbucks 65rb cash kemarin")
        response = httpx.post(
            f"{webhook_server}/api/webhook",
            json=payload,
        )
        assert response.status_code == 200
        reply = captured_messages[0]["text"]

        # Date should be YYYY-MM-DD format
        date_match = re.search(r"Tanggal: (\d{4}-\d{2}-\d{2})", reply)
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
        assert len(captured_messages) == 1
        reply = captured_messages[0]["text"]
        assert "tidak dikenali" in reply or "error" in reply.lower() or "Error" in reply

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
    """Verify rows are actually written to Google Sheets."""

    def test_expense_row_appended(
        self, webhook_server: str, captured_messages
    ):
        # Get current row count
        from api.webhook import get_worksheet

        ws = get_worksheet()
        rows_before = len(ws.get_all_values())

        payload = make_telegram_update("beli air mineral 5000 cash")
        response = httpx.post(
            f"{webhook_server}/api/webhook",
            json=payload,
        )
        assert response.status_code == 200
        assert "✅" in captured_messages[0]["text"] or "Tercatat" in captured_messages[0]["text"]

        # Verify new row was written
        rows_after = len(ws.get_all_values())
        assert rows_after == rows_before + 1

        # Verify last row content
        last_row = ws.get_all_values()[-1]
        assert last_row[6] == "5000" or int(last_row[6]) == 5000  # amount column

    def test_batch_rows_appended(
        self, webhook_server: str, captured_messages
    ):
        from api.webhook import get_worksheet

        ws = get_worksheet()
        rows_before = len(ws.get_all_values())

        payload = make_telegram_update(
            "makan bakso 24000 cash\ngrab ke kantor 35000 gopay"
        )
        response = httpx.post(
            f"{webhook_server}/api/webhook",
            json=payload,
        )
        assert response.status_code == 200

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
        assert "✅" in reply
        assert "2/2" in reply

    def test_batch_partial_failure(self, webhook_server: str, captured_messages):
        """One valid expense + one nonsense → valid saved, failure reported."""
        payload = make_telegram_update(
            "makan siang nasi goreng 50000 gopay\nhello how are you"
        )
        response = httpx.post(f"{webhook_server}/api/webhook", json=payload)
        assert response.status_code == 200
        assert len(captured_messages) == 1
        reply = captured_messages[0]["text"]
        assert "✅" in reply
        assert "❌" in reply

    def test_batch_blank_lines_ignored(self, webhook_server: str, captured_messages):
        """Blank lines between expenses are silently ignored."""
        payload = make_telegram_update(
            "makan bakso 24000 cash\n\n\ngrab ke kantor 35000 gopay"
        )
        response = httpx.post(f"{webhook_server}/api/webhook", json=payload)
        assert response.status_code == 200
        reply = captured_messages[0]["text"]
        assert "2/2" in reply

    def test_batch_over_limit(self, webhook_server: str, captured_messages):
        """Message exceeding MAX_BATCH_LINES returns an error without sheet writes."""
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

    def test_single_line_reply_format_unchanged(
        self, webhook_server: str, captured_messages
    ):
        """Single-line input still returns the full field-by-field receipt."""
        payload = make_telegram_update("beli air mineral 5000 cash")
        response = httpx.post(f"{webhook_server}/api/webhook", json=payload)
        assert response.status_code == 200
        reply = captured_messages[0]["text"]
        assert "Tanggal:" in reply
        assert "Deskripsi:" in reply
        assert "Kategori:" in reply
        assert "Type:" in reply
        assert "Source:" in reply
        assert "Jumlah:" in reply