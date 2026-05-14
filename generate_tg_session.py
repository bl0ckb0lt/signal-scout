"""
Run this once locally to generate your Telegram session string.
Store the output as GitHub Secret: TELEGRAM_SESSION_STRING

Usage:
  pip install telethon
  python generate_tg_session.py

You'll be prompted for your phone number + Telegram login code.
The session string printed at the end never expires unless you
log out from Telegram → Settings → Devices.
"""

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id   = input("Enter TELEGRAM_API_ID (from my.telegram.org): ").strip()
api_hash = input("Enter TELEGRAM_API_HASH: ").strip()

with TelegramClient(StringSession(), int(api_id), api_hash) as client:
    print("\n✅ Session string (add to GitHub Secrets as TELEGRAM_SESSION_STRING):\n")
    print(client.session.save())
