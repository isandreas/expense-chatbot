"""
Unit tests for batch expense parsing logic.
These tests mock call_ai_with_retry so no real AI or network calls are made.
"""
import json
from unittest.mock import patch

import pytest

from api.webhook import MAX_BATCH_LINES, PARSE_CONFIRM_CALLBACK, extract_expense_lines, handle_update, parse_single_expense

VALID_RESPONSE = json.dumps({
    "date": "2026-04-30",
    "description": "Test expense",
    "category": "FnB",
    "type": "needs",
    "tag": "",
    "source": "Cash",
    "amount": 25000,
})

QUOTED_DESC_RESPONSE = json.dumps({
    "date": "2026-04-30",
    "description": "Toko Desa - Ultramilk 1L dan Roti Kasino",
    "category": "Groceries",
    "type": "needs",
    "tag": "",
    "source": "Cash",
    "amount": 30000,
})


class TestParseSingleExpense:
    def test_valid_response_returns_parsed_dict(self):
        with patch("api.webhook.call_ai_with_retry", return_value=VALID_RESPONSE):
            result = parse_single_expense("lunch 25000 cash", "2026-04-30")
        assert result["amount"] == 25000
        assert result["description"] == "Test expense"
        assert result["category"] == "FnB"

    def test_amount_coerced_to_int(self):
        response = json.dumps({
            "date": "2026-04-30", "description": "x", "category": "FnB",
            "type": "needs", "tag": "", "source": "Cash", "amount": 25000.0,
        })
        with patch("api.webhook.call_ai_with_retry", return_value=response):
            result = parse_single_expense("lunch 25000 cash", "2026-04-30")
        assert isinstance(result["amount"], int)
        assert result["amount"] == 25000

    def test_error_response_raises_value_error(self):
        with patch("api.webhook.call_ai_with_retry", return_value='{"error": "invalid_input"}'):
            with pytest.raises(ValueError, match="invalid_input"):
                parse_single_expense("hello world", "2026-04-30")

    def test_missing_required_fields_raises_value_error(self):
        incomplete = json.dumps({"date": "2026-04-30", "amount": 5000})
        with patch("api.webhook.call_ai_with_retry", return_value=incomplete):
            with pytest.raises(ValueError, match="missing fields"):
                parse_single_expense("something", "2026-04-30")

    def test_non_numeric_amount_raises_value_error(self):
        bad_amount = json.dumps({
            "date": "2026-04-30", "description": "x", "category": "FnB",
            "type": "needs", "tag": "", "source": "Cash", "amount": "fifty",
        })
        with patch("api.webhook.call_ai_with_retry", return_value=bad_amount):
            with pytest.raises(ValueError, match="amount is not numeric"):
                parse_single_expense("something", "2026-04-30")

    def test_malformed_json_raises_decode_error(self):
        with patch("api.webhook.call_ai_with_retry", return_value="not json {{{"):
            with pytest.raises(json.JSONDecodeError):
                parse_single_expense("something", "2026-04-30")

    def test_quoted_description_preserved_as_is(self):
        """When AI returns the quoted text verbatim, parse_single_expense keeps it."""
        with patch("api.webhook.call_ai_with_retry", return_value=QUOTED_DESC_RESPONSE):
            result = parse_single_expense(
                '"Toko Desa - Ultramilk 1L dan Roti Kasino" cash 30000', "2026-04-30"
            )
        assert result["description"] == "Toko Desa - Ultramilk 1L dan Roti Kasino"


class TestLineSplitting:
    """Verify the line segmentation logic used in handle_update."""

    def test_blank_lines_filtered(self):
        text = "expense 1\n\n  \nexpense 2\n"
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        assert lines == ["expense 1", "expense 2"]

    def test_single_line_not_batch(self):
        text = "makan siang 50000 cash"
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        assert len(lines) == 1

    def test_two_lines_is_batch(self):
        text = "expense 1\nexpense 2"
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        assert len(lines) == 2

    def test_over_limit_detection(self):
        lines = [f"expense {i}" for i in range(MAX_BATCH_LINES + 1)]
        assert len(lines) > MAX_BATCH_LINES

    def test_fix_prefix_and_numbering_are_normalized(self):
        text = "Perbaikan:\n1. makan bakso 24000 cash\n2) grab ke kantor 35000 gopay"
        lines = extract_expense_lines(text)
        assert lines == [
            "makan bakso 24000 cash",
            "grab ke kantor 35000 gopay",
        ]

    def test_number_like_amount_not_treated_as_list_number(self):
        text = "1.500.000 transfer BCA"
        lines = extract_expense_lines(text)
        assert lines == ["1.500.000 transfer BCA"]


class TestConstants:
    def test_max_batch_lines_is_positive(self):
        assert MAX_BATCH_LINES > 0

    def test_max_batch_lines_is_twenty(self):
        assert MAX_BATCH_LINES == 20


class TestFixTemplateButtons:
    def test_fix_command_sends_buttons(self):
        body = {
            "message": {
                "chat": {"id": 1},
                "from": {"username": "tester"},
                "text": "/fix",
            }
        }
        with patch("api.webhook.send_message") as mock_send:
            handle_update(body)

        assert mock_send.call_count == 1
        _, kwargs = mock_send.call_args
        assert "reply_markup" in kwargs
        assert "inline_keyboard" in kwargs["reply_markup"]

    def test_callback_template_sends_template_text(self):
        body = {
            "callback_query": {
                "id": "cb_123",
                "data": "fix_template_multi",
                "message": {"chat": {"id": 99}},
            }
        }
        with patch("api.webhook.send_message") as mock_send, patch("api.webhook.answer_callback_query") as mock_answer:
            handle_update(body)

        mock_send.assert_called_once()
        args, _ = mock_send.call_args
        assert args[0] == 99
        assert "Perbaikan:" in args[1]
        mock_answer.assert_called_once_with("cb_123")

    def test_single_success_reply_has_confirmation_buttons(self):
        body = {
            "message": {
                "chat": {"id": 77},
                "from": {"username": "tester"},
                "text": "makan 25000 cash",
            }
        }
        parsed = {
            "date": "2026-04-30",
            "description": "makan",
            "category": "FnB",
            "type": "needs",
            "tag": "",
            "source": "Cash",
            "amount": 25000,
        }
        with patch("api.webhook.parse_single_expense", return_value=parsed), patch("api.webhook.get_worksheet") as mock_ws, patch("api.webhook.send_message") as mock_send:
            handle_update(body)

        mock_ws.return_value.append_row.assert_called_once()
        _, kwargs = mock_send.call_args
        keyboard = kwargs["reply_markup"]["inline_keyboard"][0]
        callback_data = {button["callback_data"] for button in keyboard}
        assert PARSE_CONFIRM_CALLBACK in callback_data
        assert "fix_template_single" in callback_data

    def test_callback_confirm_sends_ack_message(self):
        body = {
            "callback_query": {
                "id": "cb_ok",
                "data": PARSE_CONFIRM_CALLBACK,
                "message": {"chat": {"id": 44}},
            }
        }
        with patch("api.webhook.send_message") as mock_send, patch("api.webhook.answer_callback_query") as mock_answer:
            handle_update(body)

        mock_send.assert_called_once()
        args, _ = mock_send.call_args
        assert args[0] == 44
        assert "sudah sesuai" in args[1]
        mock_answer.assert_called_once_with("cb_ok")
