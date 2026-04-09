# Expense Chatbot

A Telegram bot that automatically logs expenses to Google Sheets using AI-powered natural language parsing.

## Tech Stack

| Component            | Technology                               |
| -------------------- | ---------------------------------------- |
| Bot Platform         | Telegram Bot API (`python-telegram-bot`) |
| AI Parser (primary)  | Groq (`llama-3.3-70b-versatile`)         |
| AI Parser (fallback) | Google Gemini (`gemini-2.0-flash`)       |
| Database             | Google Sheets (`gspread`)                |
| Auth                 | Google Service Account                   |
| Runtime              | Python 3.12                              |
| Container            | Docker                                   |

## Flow

```
User (Telegram)
    │
    │  "makan bakso 2 mangkok 24000 cash"
    ▼
Telegram Bot
    │
    ▼
AI Parser (with retry)
    ├─ Attempt 1-3: Groq (llama-3.3-70b)
    │  └─ JSON mode enabled, no markdown stripping needed
    └─ Attempt 4:   Gemini (2.0-flash) — fallback
       └─ Strips markdown fences if present
    │
    ▼
JSON Output
    {
      "date": "2026-03-13",
      "description": "Bakso 2 bowls",
      "category": "FnB",
      "type": "wants",
      "tag": "",
      "source": "Cash",
      "amount": 24000
    }
    │
    ▼
Google Sheets (append row)
    │
    ▼
Reply to User
    ✅ Logged!
    Date: 2026-03-13
    Category: FnB
    Amount: Rp24,000
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

- Python 3.12+ (via [pyenv](https://github.com/pyenv/pyenv))
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- Groq API Key from [console.groq.com](https://console.groq.com/keys)
- Google Gemini API Key from [aistudio.google.com](https://aistudio.google.com/apikey)
- Google Service Account `credentials.json` with Sheets & Drive API enabled

### Installation

```bash
# Clone repo
git clone <repo-url>
cd expense-chatbot

# Setup Python environment
pyenv install 3.12.0
pyenv local 3.12.0
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Place your Google Service Account credentials
cp /path/to/your/credentials.json .

# Run
python bot.py
```

### Docker

```bash
docker build -t expense-chatbot .
docker run --env-file .env -v $(pwd)/credentials.json:/app/credentials.json expense-chatbot
```

## License

MIT
