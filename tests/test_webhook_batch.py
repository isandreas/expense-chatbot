"""
Unit tests for batch expense parsing logic.
These tests mock call_ai_with_retry so no real AI or network calls are made.
"""
import json
import os
from unittest.mock import patch

import pytest

from api.webhook import (CANCEL_CALLBACK, CONFIRM_CALLBACK, MAX_BATCH_LINES,
                         _resolve_fix_value, extract_expense_lines,
                         handle_update, parse_all_fix_commands,
                         parse_fix_command, parse_single_expense)

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

AUTHORIZED_USER_ID = int(os.getenv("TELEGRAM_USER_ID", "123456789"))


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
        text = "1. makan bakso 24000 cash\n2) grab ke kantor 35000 gopay"
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


class TestFixCommand:
    """Unit tests for parse_fix_command and parse_all_fix_commands."""

    def test_single_field_update(self):
        row_idx, updates = parse_fix_command("fix 1 cat=Transport", 2)
        assert row_idx == 0
        assert updates == {"category": "Transport"}

    def test_multiple_fields(self):
        row_idx, updates = parse_fix_command("fix 2 cat=FnB src=BCA", 3)
        assert row_idx == 1
        assert updates == {"category": "FnB", "source": "BCA"}

    def test_1based_row_converts_to_0based(self):
        row_idx, _ = parse_fix_command("fix 3 type=wants", 5)
        assert row_idx == 2

    def test_amount_coerced_to_int(self):
        row_idx, updates = parse_fix_command("fix 1 amount=50000", 1)
        assert row_idx == 0
        assert updates == {"amount": 50000}

    def test_invalid_amount_returns_error(self):
        row_idx, hint = parse_fix_command("fix 1 amount=abc", 1)
        assert row_idx is None
        assert "amount" in hint

    def test_row_out_of_range_returns_error(self):
        row_idx, hint = parse_fix_command("fix 5 cat=FnB", 3)
        assert row_idx is None
        assert hint

    def test_no_fields_returns_error(self):
        row_idx, hint = parse_fix_command("fix 1", 1)
        assert row_idx is None

    def test_not_fix_prefix_returns_error(self):
        row_idx, hint = parse_fix_command("makan siang 50000", 1)
        assert row_idx is None

    def test_shorthand_aliases(self):
        row_idx, updates = parse_fix_command("fix 1 cat=FnB src=OVO desc=lunch type=wants", 2)
        assert row_idx == 0
        assert updates == {
            "category": "FnB",
            "source": "OVO",
            "description": "lunch",
            "type": "wants",
        }

    def test_invalid_type_returns_error(self):
        row_idx, hint = parse_fix_command("fix 1 type=luxury", 2)
        assert row_idx is None
        assert "needs" in hint or "wants" in hint

    def test_invalid_category_returns_error(self):
        row_idx, hint = parse_fix_command("fix 1 cat=Food", 2)
        assert row_idx is None
        assert hint

    def test_date_iso_format_accepted(self):
        row_idx, updates = parse_fix_command("fix 1 date=2026-05-01", 2)
        assert row_idx == 0
        assert updates == {"date": "2026-05-01"}

    def test_date_kemarin_resolves(self):
        row_idx, updates = parse_fix_command("fix 1 date=kemarin", 2, today="2026-05-25")
        assert row_idx == 0
        assert updates == {"date": "2026-05-24"}

    def test_date_hari_ini_resolves(self):
        row_idx, updates = parse_fix_command("fix 1 date=hari ini", 1, today="2026-05-25")
        # "hari ini" contains a space so it won't come through token splitting — stays unsupported via token path
        # Instead test the resolver directly
        from api.webhook import _resolve_fix_value
        ok, val, _ = _resolve_fix_value("date", "hari ini", "2026-05-25")
        assert ok
        assert val == "2026-05-25"

    def test_date_invalid_returns_error(self):
        row_idx, hint = parse_fix_command("fix 1 date=kemarin-lusa", 2, today="2026-05-25")
        assert row_idx is None
        assert "YYYY-MM-DD" in hint or "kemarin" in hint

    def test_desc_quoted_with_spaces(self):
        row_idx, updates = parse_fix_command('fix 1 desc="Toko Desa - Ultramilk 1L"', 2)
        assert row_idx == 0
        assert updates == {"description": "Toko Desa - Ultramilk 1L"}

    def test_desc_quoted_with_spaces_plus_other_field(self):
        row_idx, updates = parse_fix_command('fix 1 desc="Makan siang nasi goreng" cat=FnB', 2)
        assert row_idx == 0
        assert updates["description"] == "Makan siang nasi goreng"
        assert updates["category"] == "FnB"

    def test_multiline_all_fix(self):
        results, err = parse_all_fix_commands("fix 1 src=BNI\nfix 2 cat=Transport", 3)
        assert err == ""
        assert results == [(0, {"source": "BNI"}), (1, {"category": "Transport"})]

    def test_multiline_mixed_returns_error(self):
        results, err = parse_all_fix_commands("fix 1 cat=FnB\nmakan siang 50000", 2)
        assert not results

    def test_empty_text_returns_error(self):
        results, err = parse_all_fix_commands("", 3)
        assert not results
        assert err


class TestConfirmationFlow:
    """Tests for the pre-commit staging flow with Redis and inline buttons."""

    PARSED = {
        "date": "2026-04-30",
        "description": "Test expense",
        "category": "FnB",
        "type": "needs",
        "tag": "",
        "source": "Cash",
        "amount": 25000,
    }

    def test_new_expense_stages_and_sends_preview_not_write(self):
        """Expenses must be staged in Redis, NOT written to sheet immediately."""
        body = {
            "message": {
                "chat": {"id": 77},
                "from": {"id": AUTHORIZED_USER_ID, "username": "tester"},
                "text": "makan 25000 cash",
            }
        }
        with (
            patch("api.webhook.parse_single_expense", return_value=self.PARSED),
            patch("api.webhook.redis_set_pending") as mock_set,
            patch("api.webhook.redis_set_preview_msg"),
            patch("api.webhook.send_message_with_buttons", return_value=999) as mock_buttons,
            patch("api.webhook.get_worksheet") as mock_ws,
        ):
            handle_update(body)

        mock_set.assert_called_once_with(77, [self.PARSED])
        mock_buttons.assert_called_once()
        mock_ws.assert_not_called()

    def test_confirm_callback_writes_to_sheet_and_clears(self):
        """Tapping ✅ Simpan must write pending rows and remove buttons."""
        body = {
            "callback_query": {
                "id": "cb_confirm_123",
                "from": {"id": AUTHORIZED_USER_ID, "is_bot": False},
                "data": CONFIRM_CALLBACK,
                "message": {"chat": {"id": 42}, "message_id": 99},
            }
        }
        with (
            patch("api.webhook.redis_get_pending", return_value=[self.PARSED]),
            patch("api.webhook.redis_del_pending") as mock_del,
            patch("api.webhook.get_worksheet") as mock_ws,
            patch("api.webhook.answer_callback_query") as mock_answer,
            patch("api.webhook.clear_message_buttons") as mock_clear,
            patch("api.webhook.send_message") as mock_send,
        ):
            handle_update(body)

        mock_ws.return_value.append_rows.assert_called_once_with([
            ["2026-04-30", "Test expense", "FnB", "needs", "", "Cash", 25000]
        ])
        mock_del.assert_called_once_with(42)
        mock_answer.assert_called_once_with("cb_confirm_123")
        mock_clear.assert_called_once_with(42, 99)
        mock_send.assert_called_once()
        assert "tersimpan" in mock_send.call_args[0][1]

    def test_cancel_callback_discards_pending(self):
        """Tapping ❌ Batal must delete pending without writing to sheet."""
        body = {
            "callback_query": {
                "id": "cb_cancel_456",
                "from": {"id": AUTHORIZED_USER_ID, "is_bot": False},
                "data": CANCEL_CALLBACK,
                "message": {"chat": {"id": 55}, "message_id": 88},
            }
        }
        with (
            patch("api.webhook.redis_del_pending") as mock_del,
            patch("api.webhook.answer_callback_query") as mock_answer,
            patch("api.webhook.clear_message_buttons") as mock_clear,
            patch("api.webhook.send_message") as mock_send,
            patch("api.webhook.get_worksheet") as mock_ws,
        ):
            handle_update(body)

        mock_del.assert_called_once_with(55)
        mock_answer.assert_called_once_with("cb_cancel_456")
        mock_clear.assert_called_once_with(55, 88)
        mock_ws.assert_not_called()
        assert "Dibatalkan" in mock_send.call_args[0][1]

    def test_confirm_with_no_pending_sends_error(self):
        """Confirming with no pending state should inform the user gracefully."""
        body = {
            "callback_query": {
                "id": "cb_empty",
                "from": {"id": AUTHORIZED_USER_ID, "is_bot": False},
                "data": CONFIRM_CALLBACK,
                "message": {"chat": {"id": 10}, "message_id": 1},
            }
        }
        with (
            patch("api.webhook.redis_get_pending", return_value=None),
            patch("api.webhook.answer_callback_query") as mock_answer,
            patch("api.webhook.send_message") as mock_send,
            patch("api.webhook.get_worksheet") as mock_ws,
        ):
            handle_update(body)

        mock_ws.assert_not_called()
        mock_answer.assert_called_once_with("cb_empty")
        assert "pending" in mock_send.call_args[0][1].lower()

    def test_fix_command_updates_pending_and_resends_preview(self):
        """fix N field=value must update the staged row and re-show preview."""
        pending = [dict(self.PARSED)]
        body = {
            "message": {
                "chat": {"id": 77},
                "from": {"id": AUTHORIZED_USER_ID, "username": "tester"},
                "text": "fix 1 cat=Transport type=needs",
            }
        }
        with (
            patch("api.webhook.redis_get_pending", return_value=pending),
            patch("api.webhook.redis_set_pending") as mock_set,
            patch("api.webhook.redis_get_preview_msg", return_value=None),
            patch("api.webhook.redis_set_preview_msg"),
            patch("api.webhook.send_message_with_buttons", return_value=999) as mock_buttons,
        ):
            handle_update(body)

        saved = mock_set.call_args[0][1]
        assert saved[0]["category"] == "Transport"
        mock_buttons.assert_called_once()

    def test_multiline_fix_applied_atomically(self):
        """Multiple fix lines must all be applied in one preview cycle."""
        pending = [
            dict(self.PARSED),
            {**self.PARSED, "description": "Second", "amount": 10000},
        ]
        body = {
            "message": {
                "chat": {"id": 7},
                "from": {"id": AUTHORIZED_USER_ID, "username": "tester"},
                "text": "fix 1 src=BNI\nfix 2 cat=Transport",
            }
        }
        with (
            patch("api.webhook.redis_get_pending", return_value=pending),
            patch("api.webhook.redis_set_pending") as mock_set,
            patch("api.webhook.redis_get_preview_msg", return_value=None),
            patch("api.webhook.redis_set_preview_msg"),
            patch("api.webhook.send_message_with_buttons", return_value=999),
        ):
            handle_update(body)

        saved = mock_set.call_args[0][1]
        assert saved[0]["source"] == "BNI"
        assert saved[1]["category"] == "Transport"

    def test_mixed_fix_and_expense_rejected(self):
        """A message mixing fix commands with regular text must be rejected."""
        body = {
            "message": {
                "chat": {"id": 3},
                "from": {"id": AUTHORIZED_USER_ID, "username": "tester"},
                "text": "fix 1 cat=FnB\nmakan siang 50000",
            }
        }
        with (
            patch("api.webhook.send_message") as mock_send,
            patch("api.webhook.parse_single_expense") as mock_parse,
        ):
            handle_update(body)

        mock_parse.assert_not_called()
        assert mock_send.call_count == 1
        assert "campuran" in mock_send.call_args[0][1]

    def test_rejects_unauthorized_user(self):
        """Unauthorized user must receive rejection and no parsing/staging occurs."""
        body = {
            "message": {
                "chat": {"id": 888},
                "from": {"id": 999999999, "username": "intruder"},
                "text": "makan 25000 cash",
            }
        }
        with (
            patch("api.webhook.send_message") as mock_send,
            patch("api.webhook.parse_single_expense") as mock_parse,
            patch("api.webhook.redis_set_pending") as mock_set,
        ):
            handle_update(body)

        mock_parse.assert_not_called()
        mock_set.assert_not_called()
        assert mock_send.call_count == 1
        assert "unauthorized" in mock_send.call_args[0][1].lower()
