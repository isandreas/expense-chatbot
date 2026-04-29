# Expense Chatbot

A Telegram bot that automatically logs expenses to Google Sheets using AI-powered natural language parsing. Runs as a Vercel serverless function with webhook.

## Tech Stack

| Component            | Technology                         |
| -------------------- | ---------------------------------- |
| Bot Platform         | Telegram Bot API (Webhook)         |
| AI Parser (primary)  | Groq (`llama-3.3-70b-versatile`)   |
| AI Parser (fallback) | Google Gemini (`gemini-2.0-flash`) |
| Database             | Google Sheets (`gspread`)          |
| Auth                 | Google Service Account             |
| Runtime              | Python 3.12 (Vercel Serverless)    |
| Hosting              | Vercel                             |

## Flow

```
User (Telegram)
    │
    │  "makan bakso 2 mangkok 24000 cash"
    ▼
Telegram
    │
    │  POST /api/webhook
    ▼
Vercel Function (api/webhook.py)
    │
    ├─ Parse incoming update
    ├─ AI Parser (with retry)
    │  ├─ Attempt 1-3: Groq (llama-3.3-70b)
    │  └─ Attempt 4: Gemini (2.0-flash)
    ├─ Append row to Google Sheets
    ├─ Send reply using Telegram sendMessage API
    └─ Return HTTP 200 to avoid Telegram retries
```

## Sheet Columns

| A    | B           | C        | D    | E   | F      | G      |
| ---- | ----------- | -------- | ---- | --- | ------ | ------ |
| date | description | category | type | tag | source | amount |

## Supported Categories

`Groceries` · `Supplies` · `Transport` · `Utilities` · `Entertainment` · `Health` · `FnB` · `Shopping` · `Bill` · `Donation` · `Social` · `Other`

## Supported Input

- Bahasa Indonesia, English, or mixed
- Slang supported: "65rb", "35 ribu", "2jt"
- Auto-infers date, category, and payment method

## Setup

### Prerequisites

- Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- Groq API Key from [console.groq.com](https://console.groq.com/keys)
- Google Gemini API Key from [aistudio.google.com](https://aistudio.google.com/apikey)
- Google Service Account `credentials.json` with Sheets & Drive API enabled
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

# Set environment variables in Vercel dashboard or CLI:
vercel env add TELEGRAM_BOT_TOKEN
vercel env add GROQ_API_KEY
vercel env add GEMINI_API_KEY
vercel env add SHEET_NAME              # default: "Expense Tracker"
vercel env add GOOGLE_CREDENTIALS_BASE64  # base64-encoded credentials.json

# To encode your credentials.json:
# base64 -i credentials.json | tr -d '\n'

# Redeploy after setting env vars
vercel --prod
```

### Set Webhook

After deploying, register the webhook with Telegram:

```bash
# Install dotenv for the script (local only)
pip install python-dotenv

# Set webhook
python set_webhook.py https://your-app.vercel.app

# To remove webhook
python set_webhook.py --delete
```

### Local Development

```bash
# Setup Python environment
pyenv install 3.12.0
pyenv local 3.12.0
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run webhook E2E tests locally
pytest tests/test_webhook_e2e.py -v
```

## Testing

- E2E tests are in `tests/test_webhook_e2e.py` and use `httpx`.
- The suite starts a local webhook server and posts Telegram-like payloads.
- Tests are integration-style and use real provider credentials from `.env`.

## License

MIT
