import json
import logging
import os
import time
from datetime import datetime

import gspread
from dotenv import load_dotenv
from google import genai
from google.oauth2.service_account import Credentials
from groq import Groq
from telegram import Update
from telegram.ext import (ApplicationBuilder, CommandHandler, ContextTypes,
                          MessageHandler, filters)

load_dotenv()

# Config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SHEET_NAME = os.getenv("SHEET_NAME", "Expense Tracker")

# AI clients setup
groq_client = Groq(api_key=GROQ_API_KEY)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

GROQ_MODEL = "llama-3.3-70b-versatile"
GEMINI_MODEL = "gemini-2.0-flash"

# Expense parsing prompt (multilingual + Indonesian slang)
PARSING_PROMPT = """
You are an accurate expense parser for a personal expense tracking app.
Parse the following user input in Bahasa Indonesia, English, or mixed (slang OK):

"$USER_INPUT"

Strict rules:
- Output ONLY valid JSON, no extra text, no markdown, no explanation.
- Required fields: date, description, category, type, tag, source, amount
- date: format "YYYY-MM-DD". Use today "$TODAY" if not mentioned. Default to Jakarta (WIB) timezone.
- description: short summary of the input (max 64 chars) in English. Do not translate product names or place names. If the user wraps text in "{description}", use that text as-is for the description without summarizing.
- category: pick one or infer: Groceries, Supplies, Transport, Utilities, Entertainment, Health, FnB, Shopping, Bill, Donation, Social, Other. Prefer Groceries for home food, Supplies for non-food household items.
- type: "needs" if essential (daily meals, work transport, bills, household necessities), "wants" if discretionary (eating out, entertainment, impulse shopping, luxury). Prioritize needs/wants from the input if explicitly stated.
- tag: highlighted tag if present (e.g. urgent, luxury, refund, friend-split), or "" if none.
- source: payment method: Cash, BCA, BNI, CIMB, GoPay, Credit Card, etc. Infer if not mentioned.
- amount: integer without Rp, commas, or dots (e.g. 50000 for Rp50.000). Convert foreign currencies to IDR at current rates if needed.

If the input is unclear or not an expense → return {"error": "invalid_input"}

Example output:
{"date":"2026-03-12","category":"Groceries","type":"needs","tag":"","source":"Cash","amount":45000,"description":"Rice and vegetables"}
"""

# Gspread setup
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]
creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
client = gspread.authorize(creds)
worksheet = client.open(SHEET_NAME).sheet1

# Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


def call_groq(prompt: str) -> str:
    """Call Groq API with JSON mode."""
    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content.strip()


def call_gemini(prompt: str) -> str:
    """Call Gemini API as last resort fallback."""
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )
    json_str = response.text.strip()
    if json_str.startswith("```"):
        json_str = json_str.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json_str


async def call_ai_with_retry(prompt: str, update: Update) -> str:
    """
    Try Groq 3x, then fallback to Gemini 1x (4th attempt).
    Total: 4 attempts max.
    """
    # Attempt 1-3: Groq
    for attempt in range(3):
        try:
            logger.info(f"Groq attempt {attempt + 1}/3...")
            return call_groq(prompt)
        except Exception as e:
            logger.warning(f"Groq attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                wait = 5 * (attempt + 1)
                await update.message.reply_text(f"⏳ Groq sibuk, retry dalam {wait}s...")
                time.sleep(wait)

    # Attempt 4: Gemini fallback
    try:
        logger.info("Falling back to Gemini (attempt 4/4)...")
        await update.message.reply_text("⏳ Switching ke Gemini...")
        return call_gemini(prompt)
    except Exception as e:
        logger.error(f"Gemini fallback also failed: {e}")
        raise Exception("Semua AI provider gagal. Coba lagi nanti.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Halo! Kirim pesan expense seperti:\n"
        '"Beli makan siang 65rb pake OVO hari ini"\n'
        '"Transport Gojek 35 ribu kemarin cash"\n'
        "Aku akan parse & catat otomatis ke sheet."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    if not user_input:
        return

    logger.info(f"Received input: {user_input} from {update.message.from_user.username}")
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        full_prompt = PARSING_PROMPT.replace("$USER_INPUT", user_input).replace("$TODAY", today)

        # Call AI with retry (Groq x3 → Gemini x1)
        json_str = await call_ai_with_retry(full_prompt, update)

        parsed = json.loads(json_str)

        if "error" in parsed:
            await update.message.reply_text("Maaf, input tidak dikenali sebagai expense. Coba lagi ya!")
            return

        # Prepare row: A=date, B=description, C=category, D=type, E=tag, F=source, G=amount
        row = [
            parsed["date"],
            parsed["description"],
            parsed["category"],
            parsed["type"],
            parsed["tag"],
            parsed["source"],
            parsed["amount"],
        ]

        worksheet.append_row(row)

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
        await update.message.reply_text(reply)

    except json.JSONDecodeError:
        await update.message.reply_text("Parsing gagal (JSON invalid dari AI). Coba input lebih jelas.")
    except Exception as e:
        logger.error(e)
        await update.message.reply_text(f"Error: {str(e)}. Coba lagi nanti.")


def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot running... Tekan Ctrl+C untuk stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()