import base64
import json
import logging
import os
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


def send_message(chat_id: int, text: str):
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        f"{TELEGRAM_API}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req)
    log_event("INFO", "Reply sent to chat_id=%s", chat_id)


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

    # /start command
    if text == "/start":
        log_event("INFO", "Handling /start for chat_id=%s", chat_id)
        send_message(
            chat_id,
            "Halo! Kirim pesan expense seperti:\n"
            '"Beli makan siang 65rb pake OVO hari ini"\n'
            '"Transport Gojek 35 ribu kemarin cash"\n'
            "Aku akan parse & catat otomatis ke sheet.\n\n"
            "Untuk beberapa transaksi sekaligus, pisahkan dengan baris baru.",
        )
        return

    # Segment lines — blank lines are ignored
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    is_batch = len(lines) > 1

    if len(lines) > MAX_BATCH_LINES:
        send_message(
            chat_id,
            f"Terlalu banyak transaksi sekaligus (maks {MAX_BATCH_LINES} baris). "
            "Mohon kirim lebih sedikit.",
        )
        return

    wib = timezone(timedelta(hours=7))
    today = datetime.now(wib).strftime("%Y-%m-%d")

    if not is_batch:
        # --- Single expense: original detailed receipt ---
        try:
            parsed = parse_single_expense(lines[0], today)
        except ValueError:
            send_message(chat_id, "Maaf, input tidak dikenali sebagai expense. Coba lagi ya!")
            return
        except json.JSONDecodeError:
            logger.exception("Failed to decode AI JSON response")
            send_message(chat_id, "Parsing gagal (JSON invalid dari AI). Coba input lebih jelas.")
            return
        except Exception as e:
            logger.exception("Unhandled error while processing update")
            send_message(chat_id, f"Error: {str(e)}. Coba lagi nanti.")
            return

        row = [
            parsed["date"],
            parsed["description"],
            parsed["category"],
            parsed["type"],
            parsed["tag"],
            parsed["source"],
            parsed["amount"],
        ]

        try:
            worksheet = get_worksheet()
            worksheet.append_row(row)
        except Exception as e:
            logger.exception("Failed to write to Google Sheets")
            send_message(chat_id, f"Gagal menyimpan ke sheet: {str(e)}")
            return

        log_event(
            "INFO",
            "Sheet row appended chat_id=%s category=%s amount=%s",
            chat_id,
            parsed["category"],
            parsed["amount"],
        )

        reply = (
            f"✅ Tercatat!\n"
            f"Tanggal: {parsed['date']}\n"
            f"Deskripsi: {parsed['description']}\n"
            f"Kategori: {parsed['category']}\n"
            f"Type: {parsed['type']}\n"
            f"Tag: {parsed['tag'] or '-'}\n"
            f"Source: {parsed['source']}\n"
            f"Jumlah: Rp{parsed['amount']:,}\n"
        )
        send_message(chat_id, reply)
        return

    # --- Batch mode: parse each line independently ---
    successful_rows = []
    successful_parsed = []
    failures = []  # list of (line_index, line_text, reason)

    for i, line in enumerate(lines, start=1):
        try:
            parsed = parse_single_expense(line, today)
            successful_rows.append([
                parsed["date"],
                parsed["description"],
                parsed["category"],
                parsed["type"],
                parsed["tag"],
                parsed["source"],
                parsed["amount"],
            ])
            successful_parsed.append(parsed)
            log_event("INFO", "Batch line %s/%s parsed OK amount=%s", i, len(lines), parsed["amount"])
        except ValueError as e:
            failures.append((i, line, str(e)))
            log_event("WARNING", "Batch line %s failed: %s", i, e)
        except json.JSONDecodeError:
            failures.append((i, line, "JSON invalid dari AI"))
            log_event("WARNING", "Batch line %s JSON decode error", i)
        except Exception as e:
            failures.append((i, line, str(e)))
            log_event("WARNING", "Batch line %s unhandled error: %s", i, e)

    # Write all successful rows in one batch call
    if successful_rows:
        try:
            worksheet = get_worksheet()
            worksheet.append_rows(successful_rows)
            log_event(
                "INFO",
                "Batch sheet write chat_id=%s rows=%s",
                chat_id,
                len(successful_rows),
            )
        except Exception as e:
            logger.exception("Failed to batch write to Google Sheets")
            send_message(chat_id, f"Gagal menyimpan ke sheet: {str(e)}")
            return

    # Compose batch summary reply
    parts = []
    if successful_rows:
        total_amount = sum(p["amount"] for p in successful_parsed)
        parts.append(f"✅ Tersimpan: {len(successful_rows)}/{len(lines)} transaksi")
        parts.append(f"💰 Total: Rp{total_amount:,}")
        for p in successful_parsed:
            parts.append(f"  • {p['description']} — Rp{p['amount']:,} ({p['category']})")

    if failures:
        parts.append(f"\n❌ Gagal: {len(failures)}/{len(lines)} transaksi")
        for idx, line_text, reason in failures:
            short = line_text[:40] + "..." if len(line_text) > 40 else line_text
            parts.append(f"  • Baris {idx}: \"{short}\" → {reason}")

    send_message(chat_id, "\n".join(parts))


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
