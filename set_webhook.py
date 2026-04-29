"""
Set Telegram webhook to point to your Vercel deployment.

Usage:
    python set_webhook.py https://your-app.vercel.app

This will register: https://your-app.vercel.app/api/webhook
"""

import json
import os
import sys
import urllib.request

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


def set_webhook(base_url: str):
    webhook_url = f"{base_url.rstrip('/')}/api/webhook"
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"

    payload = json.dumps({"url": webhook_url}).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    result = json.loads(resp.read())
    print(f"Webhook URL: {webhook_url}")
    print(f"Response: {json.dumps(result, indent=2)}")


def delete_webhook():
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook"
    req = urllib.request.Request(api_url)
    resp = urllib.request.urlopen(req)
    result = json.loads(resp.read())
    print(f"Delete webhook response: {json.dumps(result, indent=2)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python set_webhook.py <vercel-url>")
        print("       python set_webhook.py --delete")
        sys.exit(1)

    if sys.argv[1] == "--delete":
        delete_webhook()
    else:
        set_webhook(sys.argv[1])
