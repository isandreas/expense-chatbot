import base64
import json
import logging
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler

import gspread
from google import genai
from google.oauth2.service_account import Credentials
from groq import Groq

# Logging
logger = logging.getLogger("api.webhook")
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(stdout_handler)


def log_event(level: str, message: str, *args):
    getattr(logger, level.lower(), logger.info)(message, *args)

# Config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SHEET_NAME = os.getenv("SHEET_NAME", "Expense Tracker")

# AI clients
groq_client = Groq(api_key=GROQ_API_KEY)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

GROQ_MODEL = "llama-3.3-70b-versatile"
GEMINI_MODEL = "gemini-2.0-flash"

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MAX_BATCH_LINES = 20
REQUIRED_FIELDS = {"date", "description", "category", "type", "tag", "source", "amount"}

# Callback data constants for inline keyboard buttons
CONFIRM_CALLBACK = "cb_confirm"
CANCEL_CALLBACK = "cb_cancel"

# Upstash Redis REST (for pending state)
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")
PENDING_TTL = 600  # 10 minutes

# Fix command field aliases (shorthand → canonical field name)
FIELD_ALIASES = {
    "cat": "category",
    "category": "category",
    "desc": "description",
    "description": "description",
    "src": "source",
    "source": "source",
    "type": "type",
    "tag": "tag",
    "date": "date",
    "amount": "amount",
}

# Expense parsing prompt
PARSING_PROMPT = """
You are an accurate expense parser for a personal expense tracking app.
Parse the following user input in Bahasa Indonesia, English, or mixed (slang OK):

"$USER_INPUT"

Strict rules:
- Output ONLY valid JSON, no extra text, no markdown, no explanation.
- Required fields: date, description, category, type, tag, source, amount
- date: format "YYYY-MM-DD". Use today "$TODAY" if not mentioned. Default to Jakarta (WIB) timezone.
- description: short summary of the input (max 64 chars) in English. Do not translate product names or place names. If the user starts the input with a double-quoted string (e.g. "Toko Desa - Ultramilk 1L"), use that quoted text as-is for the description without summarizing or translating.
- category: pick one or infer: Groceries, Supplies, Transport, Utilities, Entertainment, Health, FnB, Shopping, Bill, Donation, Social, Other. Prefer Groceries for home food, Supplies for non-food household items including cleaning products (e.g. sabun, detergen, pel, karbol, obat pel, pembersih lantai).
- type: "needs" if essential (daily meals, work transport, bills, household necessities), "wants" if discretionary (eating out, entertainment, impulse shopping, luxury). Prioritize needs/wants from the input if explicitly stated.
- tag: highlighted tag if present (e.g. urgent, luxury, refund, friend-split), or "" if none.
- source: payment method: Cash, BCA, BNI, CIMB, GoPay, Credit Card, etc. Infer if not mentioned.
- amount: integer without Rp, commas, or dots (e.g. 50000 for Rp50.000). Convert foreign currencies to IDR at current rates if needed.

If the input is unclear or not an expense → return {"error": "invalid_input"}

Example output:
{"date":"2026-03-12","category":"Groceries","type":"needs","tag":"","source":"Cash","amount":45000,"description":"Rice and vegetables"}
"""

# Google Sheets setup
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# ── Redis helpers (Upstash REST, no extra package needed) ─────────────────────

def _redis_cmd(cmd: list):
    """Execute a single Redis command via Upstash REST API."""
    payload = json.dumps(cmd).encode("utf-8")
    req = urllib.request.Request(
        UPSTASH_REDIS_REST_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}",
        },
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return data.get("result")


def redis_get_pending(chat_id: int) -> list | None:
    """Return staged rows for chat_id, or None if no pending state exists."""
    result = _redis_cmd(["GET", f"pending:{chat_id}"])
    if result is None:
        return None
    return json.loads(result)


def redis_set_pending(chat_id: int, rows: list) -> None:
    """Stage rows for chat_id with a TTL of PENDING_TTL seconds."""
    _redis_cmd(["SET", f"pending:{chat_id}", json.dumps(rows), "EX", str(PENDING_TTL)])


def redis_del_pending(chat_id: int) -> None:
    """Delete any staged rows for chat_id."""
    _redis_cmd(["DEL", f"pending:{chat_id}"])


def redis_set_preview_msg(chat_id: int, msg_id: int) -> None:
    """Remember the message_id of the last preview sent to chat_id."""
    _redis_cmd(["SET", f"preview_msg:{chat_id}", str(msg_id), "EX", str(PENDING_TTL)])


def redis_get_preview_msg(chat_id: int) -> int | None:
    """Return the message_id of the last preview for chat_id, or None."""
    result = _redis_cmd(["GET", f"preview_msg:{chat_id}"])
    return int(result) if result else None


def get_worksheet():
    creds_b64 = os.getenv("GOOGLE_CREDENTIALS_BASE64")
    if creds_b64:
        creds_json = base64.b64decode(creds_b64)
        creds_info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    gc = gspread.authorize(creds)
    return gc.open(SHEET_NAME).sheet1


def call_groq(prompt: str) -> str:
    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content.strip()


def call_gemini(prompt: str) -> str:
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )
    json_str = response.text.strip()
    if json_str.startswith("```"):
        json_str = json_str.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json_str


def call_ai_with_retry(prompt: str) -> str:
    """Try Groq 3x, then fallback to Gemini 1x."""
    for attempt in range(3):
        try:
            log_event("INFO", "Groq attempt %s/3...", attempt + 1)
            return call_groq(prompt)
        except Exception as e:
            log_event("WARNING", "Groq attempt %s failed: %s", attempt + 1, e)

    try:
        log_event("INFO", "Falling back to Gemini (attempt 4/4)...")
        return call_gemini(prompt)
    except Exception as e:
        logger.error(f"Gemini fallback also failed: {e}")
        raise Exception("Semua AI provider gagal. Coba lagi nanti.")


def send_message(chat_id: int, text: str, reply_markup: dict | None = None):
    payload_obj = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload_obj["reply_markup"] = reply_markup
    payload = json.dumps(payload_obj).encode("utf-8")
    req = urllib.request.Request(
        f"{TELEGRAM_API}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req)
    log_event("INFO", "Reply sent to chat_id=%s", chat_id)


def answer_callback_query(callback_query_id: str):
    payload = json.dumps({"callback_query_id": callback_query_id}).encode("utf-8")
    req = urllib.request.Request(
        f"{TELEGRAM_API}/answerCallbackQuery",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req)


def send_message_with_buttons(chat_id: int, text: str) -> int:
    """Send a preview message with ✅ Simpan / ❌ Batal inline buttons. Returns message_id."""
    reply_markup = {
        "inline_keyboard": [[
            {"text": "✅ Simpan", "callback_data": CONFIRM_CALLBACK},
            {"text": "❌ Batal", "callback_data": CANCEL_CALLBACK},
        ]]
    }
    payload = json.dumps(
        {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{TELEGRAM_API}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    message_id = data.get("result", {}).get("message_id")
    log_event("INFO", "Preview sent to chat_id=%s message_id=%s", chat_id, message_id)
    return message_id


def clear_message_buttons(chat_id: int, message_id: int) -> None:
    """Remove the inline keyboard from a sent message (best-effort)."""
    payload = json.dumps({
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": {"inline_keyboard": []},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{TELEGRAM_API}/editMessageReplyMarkup",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req)
    except Exception:
        pass  # best-effort, non-critical


def extract_expense_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return lines

    normalized = []
    for line in lines:
        i = 0
        while i < len(line) and line[i].isdigit():
            i += 1
        if i > 0 and i < len(line) and line[i] in {".", ")"}:
            rest = line[i + 1:]
            if rest.startswith(" "):
                normalized.append(rest.strip())
                continue
        normalized.append(line)
    return normalized


# ── Fix command helpers ────────────────────────────────────────────────────────

VALID_CATEGORIES = {
    "groceries", "supplies", "transport", "utilities",
    "entertainment", "health", "fnb", "shopping",
    "bill", "donation", "social", "other",
}
VALID_TYPES = {"needs", "wants"}

_DATE_WORDS: dict[str, int] = {
    "kemarin": -1, "yesterday": -1,
    "hari ini": 0, "today": 0, "sekarang": 0,
    "besok": 1, "tomorrow": 1,
    "lusa": 2,
    "kemarin lusa": -2,
}

# Matches key=value pairs where value is either a double-quoted string
# (may contain spaces) or an unquoted non-whitespace token.
# Examples: cat=FnB  desc="Toko Desa - Ultramilk 1L"  amount=50000
_KV_RE = re.compile(r'(\w+)=(?:"([^"]*)"|([^\s=]+))')


def _resolve_fix_value(field: str, value: str, today: str) -> tuple[bool, object, str]:
    """Validate and coerce a fix field value.

    Returns (ok, coerced_value, error_hint).
    error_hint is an empty string when ok is True.
    """
    if field == "amount":
        # Strip thousands separators before parsing
        clean = value.replace(".", "").replace(",", "").replace("rb", "000").replace("k", "000")
        try:
            v = int(clean)
            if v <= 0:
                return False, None, "amount harus > 0"
            return True, v, ""
        except ValueError:
            return False, None, "amount harus angka (contoh: 50000)"

    if field == "date":
        low = value.strip().lower()
        if low in _DATE_WORDS:
            wib = timezone(timedelta(hours=7))
            base = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=wib)
            resolved = (base + timedelta(days=_DATE_WORDS[low])).strftime("%Y-%m-%d")
            return True, resolved, ""
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return True, value, ""
        return False, None, 'date harus YYYY-MM-DD atau kata seperti "kemarin", "hari ini"'

    if field == "type":
        low = value.lower()
        if low not in VALID_TYPES:
            return False, None, f"type harus: {', '.join(sorted(VALID_TYPES))}"
        return True, low, ""

    if field == "category":
        low = value.lower()
        if low not in VALID_CATEGORIES:
            valid_display = ", ".join(sorted(VALID_CATEGORIES, key=str.lower))
            return False, None, f"category harus salah satu: {valid_display}"
        # Return the correctly-capitalised form from the prompt
        canonical_map = {
            "fnb": "FnB", "groceries": "Groceries", "supplies": "Supplies",
            "transport": "Transport", "utilities": "Utilities",
            "entertainment": "Entertainment", "health": "Health",
            "shopping": "Shopping", "bill": "Bill", "donation": "Donation",
            "social": "Social", "other": "Other",
        }
        return True, canonical_map[low], ""

    # source, description, tag — free text, just require non-empty
    if not value.strip():
        return False, None, f"{field} tidak boleh kosong"
    return True, value.strip(), ""


def parse_fix_command(line: str, n_rows: int, today: str = "") -> tuple[int, dict] | tuple[None, str]:
    """Parse a single 'fix [N] field=value ...' line.

    Row number is 1-based (matching the preview shown to the user).
    Returns (row_idx_0based, updates_dict) on success.
    Returns (None, error_hint) on failure.
    """
    stripped = line.strip()
    if not stripped.lower().startswith("fix "):
        return None, "baris tidak dimulai dengan 'fix'"
    rest = stripped[4:].strip()
    if not rest:
        return None, "tidak ada field yang diubah"

    # Extract optional 1-based row number from the start
    row_idx = 0
    m = re.match(r'^(\d+)\s+', rest)
    if m:
        row_idx = int(m.group(1)) - 1  # convert 1-based → 0-based
        rest = rest[m.end():]
    if row_idx < 0 or row_idx >= n_rows:
        return None, f"nomor baris harus 1–{n_rows}"

    # Parse key=value pairs; quoted values (desc="...") preserve spaces
    updates: dict = {}
    for kv in _KV_RE.finditer(rest):
        field = kv.group(1)
        # group(2) = quoted value, group(3) = unquoted value
        value = kv.group(2) if kv.group(2) is not None else kv.group(3)
        canonical = FIELD_ALIASES.get(field.lower())
        if not canonical:
            continue
        ok, coerced, hint = _resolve_fix_value(canonical, value, today)
        if not ok:
            return None, hint
        updates[canonical] = coerced
    if not updates:
        return None, "tidak ada field yang valid"
    return (row_idx, updates)


def parse_all_fix_commands(text: str, n_rows: int, today: str = "") -> tuple[list, str]:
    """Parse a multi-line fix message.

    All non-blank lines must start with 'fix '.
    Returns (results, "") on success, ([], error_hint) on failure.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return [], "tidak ada perintah fix"
    if any(not l.lower().startswith("fix ") for l in lines):
        return [], "pesan campuran"
    results = []
    for line in lines:
        row_idx, payload = parse_fix_command(line, n_rows, today)
        if row_idx is None:
            return [], payload  # payload is the error hint
        results.append((row_idx, payload))
    return results, ""


def format_preview(pending: list, failures: list) -> str:
    """Build the pre-commit preview message shown to the user."""
    parts = ["📋 Review sebelum disimpan:"]
    for i, p in enumerate(pending, start=1):
        parts.append(
            f"{i}. {p['description']} — Rp{p['amount']:,} | "
            f"{p['category']} | {p['type']} | {p['source']} | {p['date']}"
        )
    if failures:
        parts.append("")
        parts.append(f"❌ Gagal diparse ({len(failures)} baris, tidak akan disimpan):")
        for idx, line_text, _ in failures:
            short = line_text[:40] + "..." if len(line_text) > 40 else line_text
            parts.append(f'  • Baris {idx}: "{short}"')
    parts.append("")
    parts.append("Koreksi: fix [N] field=value  (bisa beberapa baris sekaligus)")
    return "\n".join(parts)


def parse_single_expense(line: str, today: str) -> dict:
    """Parse one expense line via AI. Returns validated dict or raises."""
    full_prompt = PARSING_PROMPT.replace("$USER_INPUT", line).replace("$TODAY", today)
    json_str = call_ai_with_retry(full_prompt)
    parsed = json.loads(json_str)  # raises json.JSONDecodeError if malformed
    if "error" in parsed:
        raise ValueError("invalid_input")
    missing = REQUIRED_FIELDS - set(parsed.keys())
    if missing:
        raise ValueError(f"missing fields: {', '.join(sorted(missing))}")
    if not isinstance(parsed["amount"], (int, float)):
        raise ValueError("amount is not numeric")
    parsed["amount"] = int(parsed["amount"])
    return parsed


def handle_update(body: dict):
    # ── Callback query (inline button tap) ───────────────────────────────────
    callback_query = body.get("callback_query")
    if callback_query:
        cq_id = callback_query.get("id")
        cq_data = callback_query.get("data", "")
        msg = callback_query.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        message_id = msg.get("message_id")

        if not chat_id:
            if cq_id:
                answer_callback_query(cq_id)
            return

        if cq_data == CONFIRM_CALLBACK:
            pending = redis_get_pending(chat_id)
            if not pending:
                answer_callback_query(cq_id)
                send_message(chat_id, "Tidak ada transaksi pending. Kirim expense baru dulu ya.")
                return
            rows = [
                [p["date"], p["description"], p["category"], p["type"], p["tag"], p["source"], p["amount"]]
                for p in pending
            ]
            try:
                worksheet = get_worksheet()
                worksheet.append_rows(rows)
            except Exception as e:
                logger.exception("Failed to write to Google Sheets")
                answer_callback_query(cq_id)
                send_message(chat_id, f"Gagal menyimpan ke sheet: {str(e)}")
                return
            redis_del_pending(chat_id)
            answer_callback_query(cq_id)
            if message_id:
                clear_message_buttons(chat_id, message_id)
            total = sum(p["amount"] for p in pending)
            summary_lines = [f"✅ {len(pending)} transaksi tersimpan!", f"💰 Total: Rp{total:,}"]
            for p in pending:
                summary_lines.append(f"  • {p['description']} — Rp{p['amount']:,} ({p['category']})")
            send_message(chat_id, "\n".join(summary_lines))
            log_event("INFO", "Confirmed commit chat_id=%s rows=%s", chat_id, len(pending))

        elif cq_data == CANCEL_CALLBACK:
            redis_del_pending(chat_id)
            answer_callback_query(cq_id)
            if message_id:
                clear_message_buttons(chat_id, message_id)
            send_message(chat_id, "Dibatalkan. Tidak ada yang disimpan.")
            log_event("INFO", "Cancelled pending chat_id=%s", chat_id)

        else:
            answer_callback_query(cq_id)
        return

    # ── Text message ──────────────────────────────────────────────────────────
    message = body.get("message")
    if not message:
        log_event("INFO", "Webhook update ignored: no message field")
        return

    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()
    if not text:
        log_event("INFO", "Webhook update ignored: empty text for chat_id=%s", chat_id)
        return

    username = message.get("from", {}).get("username", "unknown")
    log_event("INFO", "Received: %s from %s", text, username)

    if text == "/start":
        log_event("INFO", "Handling /start for chat_id=%s", chat_id)
        send_message(
            chat_id,
            "Halo! Kirim pesan expense seperti:\n"
            '"Beli makan siang 65rb pake OVO hari ini"\n'
            '"Transport Gojek 35 ribu kemarin cash"\n'
            "Aku akan parse & catat otomatis ke sheet.\n\n"
            "Untuk beberapa transaksi sekaligus, pisahkan dengan baris baru.\n"
            "Kamu akan diminta konfirmasi sebelum disimpan.",
        )
        return

    wib = timezone(timedelta(hours=7))
    today = datetime.now(wib).strftime("%Y-%m-%d")

    # ── Fix commands ──────────────────────────────────────────────────────────
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    any_fix = any(l.lower().startswith("fix ") for l in lines)
    all_fix = bool(lines) and all(l.lower().startswith("fix ") for l in lines)

    if any_fix and not all_fix:
        send_message(
            chat_id,
            "Pesan campuran tidak didukung. Kirim perintah fix saja, atau expense baru saja.\n"
            "Contoh fix:\nfix 1 cat=FnB\nfix 3 src=BNI",
        )
        return

    if all_fix:
        pending = redis_get_pending(chat_id)
        if not pending:
            send_message(chat_id, "Tidak ada transaksi pending. Kirim expense baru dulu ya.")
            return
        result, err = parse_all_fix_commands(text, len(pending), today)
        if not result:
            if err == "pesan campuran":
                send_message(
                    chat_id,
                    "Pesan campuran tidak didukung. Kirim perintah fix saja, atau expense baru saja.\n"
                    "Contoh fix:\nfix 1 cat=FnB\nfix 3 src=BNI",
                )
            else:
                send_message(
                    chat_id,
                    f"Koreksi tidak valid: {err}\n\n"
                    "Format: fix [N] field=value\n"
                    "Field: cat, type, src, tag, date, amount, desc\n"
                    'Date: YYYY-MM-DD atau "kemarin", "hari ini"\n'
                    "Type: needs / wants",
                )
            return
        for row_idx, updates in result:
            pending[row_idx].update(updates)
        redis_set_pending(chat_id, pending)
        old_msg_id = redis_get_preview_msg(chat_id)
        if old_msg_id:
            clear_message_buttons(chat_id, old_msg_id)
        new_msg_id = send_message_with_buttons(chat_id, format_preview(pending, []))
        if new_msg_id:
            redis_set_preview_msg(chat_id, new_msg_id)
        return

    # ── New expense input ─────────────────────────────────────────────────────
    expense_lines = extract_expense_lines(text)
    if not expense_lines:
        return

    if len(expense_lines) > MAX_BATCH_LINES:
        send_message(
            chat_id,
            f"Terlalu banyak transaksi sekaligus (maks {MAX_BATCH_LINES} baris). "
            "Mohon kirim lebih sedikit.",
        )
        return

    successful_parsed: list = []
    failures: list = []

    for i, line in enumerate(expense_lines, start=1):
        try:
            parsed = parse_single_expense(line, today)
            successful_parsed.append(parsed)
            log_event("INFO", "Parsed line %s/%s amount=%s", i, len(expense_lines), parsed["amount"])
        except ValueError as e:
            failures.append((i, line, str(e)))
            log_event("WARNING", "Parse failed line %s: %s", i, e)
        except json.JSONDecodeError:
            failures.append((i, line, "JSON invalid dari AI"))
        except Exception as e:
            failures.append((i, line, str(e)))
            log_event("WARNING", "Unhandled error line %s: %s", i, e)

    if not successful_parsed:
        send_message(chat_id, "Maaf, semua input tidak dikenali sebagai expense. Coba lagi ya!")
        return

    redis_set_pending(chat_id, successful_parsed)
    msg_id = send_message_with_buttons(chat_id, format_preview(successful_parsed, failures))
    if msg_id:
        redis_set_preview_msg(chat_id, msg_id)



class handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        log_event("INFO", "Webhook POST received content_length=%s", content_length)

        try:
            update = json.loads(body)
            log_event("INFO", "Webhook payload parsed successfully")
            handle_update(update)
        except Exception as e:
            logger.exception("Webhook handler failed before response")

        # Always return 200 to Telegram to avoid retries
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode())
        log_event("INFO", "Webhook response sent status=200")

    def do_GET(self):
        log_event("INFO", "Webhook GET healthcheck request received")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "Expense bot webhook is running"}).encode())
