# Expense Chatbot

A Telegram bot that logs expenses to Google Sheets using AI-powered natural language parsing. Runs as a Vercel serverless function with webhook. Expenses are staged for review before being written to the sheet.

## Tech Stack

| Component            | Technology                         |
| -------------------- | ---------------------------------- |
| Bot Platform         | Telegram Bot API (Webhook)         |
| AI Parser (primary)  | Groq (`llama-3.3-70b-versatile`)   |
| AI Parser (fallback) | Google Gemini (`gemini-2.0-flash`) |
| Storage              | Google Sheets (`gspread`)          |
| Pending State        | Upstash Redis (REST, no SDK)       |
| Auth                 | Google Service Account             |
| Runtime              | Python 3.12 (Vercel Serverless)    |
| Hosting              | Vercel                             |

## Flow

```
User (Telegram)
    │
    │  "makan bakso 2 mangkok 24000 cash"
    │  — or multiple lines for batch input —
    ▼
Telegram
    │
    │  POST /api/webhook
    ▼
Vercel Function (api/webhook.py)
    │
    ├─ Parse incoming update
    │
    ├─ callback_query (button tap):
    │  ├─ ✅ Simpan → write pending rows to sheet → clear buttons → confirm
    │  └─ ❌ Batal  → discard pending → clear buttons → notify
    │
    ├─ "fix N field=value" message:
    │  ├─ Validate & apply corrections to pending rows (1-based N)
    │  ├─ Clear old preview buttons
    │  └─ Re-show updated preview with buttons
    │
    └─ New expense input (single or batch, max 20 lines):
       ├─ AI Parser per line (3× Groq → Gemini fallback)
       ├─ Stage successful rows in Redis (TTL 10 min)
       ├─ Show 📋 pre-commit preview with field summary
       └─ Send ✅ Simpan / ❌ Batal inline buttons
```

## Pre-commit Preview

Every expense — single or batch — is shown for review before being written to the sheet:

```
📋 Review sebelum disimpan:
1. Makan bakso — Rp24,000 | FnB | needs | Cash | 2026-05-25
2. Grab ke kantor — Rp35,000 | Transport | needs | GoPay | 2026-05-25

Koreksi: fix [N] field=value  (bisa beberapa baris sekaligus)

  ✅ Simpan    ❌ Batal
```

Tap **✅ Simpan** to write all rows at once. Tap **❌ Batal** to discard without writing.

## Fixing a Parsed Row

Before confirming, you can correct any field with a `fix` command:

```
fix 1 cat=Transport type=needs
fix 2 src=BNI
fix 1 desc="Toko Desa - Ultramilk 1L" amount=30000
```

Rules:
- Row number is **1-based** (matches the preview list)
- Multiple `fix` lines in one message are applied atomically
- Mixing `fix` lines with regular text is rejected
- After each fix, the old preview buttons are cleared and a new preview is shown

### Field shortcuts

| Shorthand | Field         |
| --------- | ------------- |
| `cat`     | `category`    |
| `desc`    | `description` |
| `src`     | `source`      |
| `type`    | `type`        |
| `tag`     | `tag`         |
| `date`    | `date`        |
| `amount`  | `amount`      |

### Field validation

| Field       | Accepted values |
| ----------- | --------------- |
| `category`  | `Groceries`, `Supplies`, `Transport`, `Utilities`, `Entertainment`, `Health`, `FnB`, `Shopping`, `Bill`, `Donation`, `Social`, `Other` (case-insensitive) |
| `type`      | `needs` or `wants` |
| `date`      | `YYYY-MM-DD`, or natural language: `kemarin`, `hari ini`, `besok`, `lusa`, `yesterday`, `today`, `tomorrow` |
| `amount`    | Integer; strips `.` `,` separators and `rb`/`k` suffixes (e.g. `50.000`, `50rb`, `50k` all → `50000`) |
| `desc`      | Any text; use double quotes to preserve spaces: `desc="Toko Desa - Ultramilk 1L"` |
| `source`    | Any non-empty text |
| `tag`       | Any text (can be empty) |

## Sheet Columns

| A    | B           | C        | D    | E   | F      | G      |
| ---- | ----------- | -------- | ---- | --- | ------ | ------ |
| date | description | category | type | tag | source | amount |

## Supported Input

- Bahasa Indonesia, English, or mixed
- Slang: `65rb`, `35 ribu`, `2jt`
- Auto-infers date, category, and payment method
- **Batch**: send multiple expenses at once, one per line (max 20); lines that fail to parse are shown in the preview but not staged
- Wrap description in double quotes to preserve it as-is: `"Toko Desa - Ultramilk 1L" cash 30000`

## Setup

### Prerequisites

- Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- Groq API Key from [console.groq.com](https://console.groq.com/keys)
- Google Gemini API Key from [aistudio.google.com](https://aistudio.google.com/apikey)
- Google Service Account `credentials.json` with Sheets & Drive API enabled
- Upstash Redis database from [console.upstash.com](https://console.upstash.com/) (free tier is sufficient)
- [Vercel CLI](https://vercel.com/docs/cli) installed

### Deploy to Vercel

```bash
# Clone repo
git clone <repo-url>
cd expense-chatbot

# Install Vercel CLI
npm i -g vercel

# Deploy
vercel

# Set environment variables (Vercel dashboard or CLI):
vercel env add TELEGRAM_BOT_TOKEN
vercel env add GROQ_API_KEY
vercel env add GEMINI_API_KEY
vercel env add SHEET_NAME                  # default: "Expenses"
vercel env add GOOGLE_CREDENTIALS_BASE64   # base64-encoded credentials.json
vercel env add UPSTASH_REDIS_REST_URL      # from Upstash console
vercel env add UPSTASH_REDIS_REST_TOKEN    # from Upstash console

# To encode your credentials.json:
# base64 -i credentials.json | tr -d '\n'

# Redeploy after setting env vars
vercel --prod
```

### Set Webhook

After deploying, register the webhook with Telegram:

```bash
python set_webhook.py https://your-app.vercel.app
```

To point the bot at a preview deployment for testing before merging:

```bash
# Deploy preview
vercel

# Point bot at preview URL
python set_webhook.py https://<preview-url>.vercel.app

# When done, restore production
python set_webhook.py https://your-app.vercel.app
```

### Local Development

```bash
pyenv install 3.12.0
pyenv local 3.12.0
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your values.

## Testing

Unit tests mock all AI and network calls — no credentials needed. E2E tests start a local HTTP server and hit real Groq/Gemini/Sheets APIs.

```bash
# Fast unit tests (no credentials needed)
pytest tests/test_webhook_batch.py -v

# Full integration tests (requires .env)
pytest tests/test_webhook_e2e.py -v
```

## License

MIT
