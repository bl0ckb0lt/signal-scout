"""
tg_alpha.py — Telegram alpha channel scanner for Signal Scout.

Reads recent messages from public Telegram alpha channels, extracts
contract addresses (Solana + EVM), returns token seeds for enrichment.

Setup (one-time):
  1. Get API credentials: https://my.telegram.org → App → api_id + api_hash
  2. Run: python generate_tg_session.py  (creates TELEGRAM_SESSION_STRING)
  3. Add to GitHub Secrets:
       TELEGRAM_API_ID
       TELEGRAM_API_HASH
       TELEGRAM_SESSION_STRING
       TG_ALPHA_CHANNELS   (comma-separated public channel usernames)

Env vars:
  TG_ALPHA_CHANNELS        comma-sep channel @usernames (required)
  TG_ALPHA_LOOKBACK_MIN    how far back to scan per run (default: 20)
  TG_ALPHA_MIN_MENTIONS    min times a CA must appear across msgs (default: 1)
  TELEGRAM_API_ID
  TELEGRAM_API_HASH
  TELEGRAM_SESSION_STRING
"""

import os, re
from datetime import datetime, timezone, timedelta

try:
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
    TELETHON_OK = True
except Exception:
    TELETHON_OK = False

# Solana: base58, 32–44 chars (excludes obvious non-CA tokens like short words)
SOLANA_RE = re.compile(r'\b([1-9A-HJ-NP-Za-km-z]{32,44})\b')
# EVM: 0x + 40 hex chars
EVM_RE    = re.compile(r'\b(0x[0-9a-fA-F]{40})\b')

# Addresses to always ignore (pump.fun program, system program, etc.)
BLOCKLIST = {
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
}


def fetch_tg_alpha_tokens():
    """
    Connect to Telegram, read recent messages from configured alpha channels,
    extract contract addresses, return seed dicts for enrichment.
    """
    if not TELETHON_OK:
        print("  tg_alpha: telethon not installed — skipping")
        return []

    api_id      = os.getenv("TELEGRAM_API_ID", "")
    api_hash    = os.getenv("TELEGRAM_API_HASH", "")
    session_str = os.getenv("TELEGRAM_SESSION_STRING", "")
    channels_raw = os.getenv("TG_ALPHA_CHANNELS", "")

    if not all([api_id, api_hash, session_str, channels_raw]):
        print("  tg_alpha: missing env vars — skipping")
        return []

    channels       = [c.strip().lstrip("@") for c in channels_raw.split(",") if c.strip()]
    lookback_min   = int(os.getenv("TG_ALPHA_LOOKBACK_MIN", "20"))
    min_mentions   = int(os.getenv("TG_ALPHA_MIN_MENTIONS", "1"))
    cutoff         = datetime.now(timezone.utc) - timedelta(minutes=lookback_min)

    # addr → {chain, count, channels, snippet}
    found: dict = {}

    try:
        with TelegramClient(StringSession(session_str), int(api_id), api_hash) as client:
            for ch in channels:
                try:
                    msgs = client.iter_messages(ch, limit=100)
                    for msg in msgs:
                        if not msg.date:
                            continue
                        msg_dt = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
                        if msg_dt < cutoff:
                            break
                        text = msg.text or msg.message or ""
                        if not text:
                            continue

                        # Solana addresses
                        for addr in SOLANA_RE.findall(text):
                            if addr in BLOCKLIST or len(addr) < 32:
                                continue
                            rec = found.setdefault(addr, {"chain": "solana", "count": 0,
                                                          "channels": set(), "snippet": ""})
                            rec["count"] += 1
                            rec["channels"].add(ch)
                            if not rec["snippet"]:
                                rec["snippet"] = text[:120].replace("\n", " ")

                        # EVM addresses
                        for addr in EVM_RE.findall(text):
                            if addr in BLOCKLIST:
                                continue
                            rec = found.setdefault(addr, {"chain": "evm", "count": 0,
                                                          "channels": set(), "snippet": ""})
                            rec["count"] += 1
                            rec["channels"].add(ch)
                            if not rec["snippet"]:
                                rec["snippet"] = text[:120].replace("\n", " ")

                except Exception as e:
                    print(f"  tg_alpha: channel {ch} error — {e}")

    except Exception as e:
        print(f"  tg_alpha: connection error — {e}")
        return []

    tokens = []
    for addr, rec in found.items():
        if rec["count"] < min_mentions:
            continue
        tokens.append({
            "chain":       rec["chain"],
            "address":     addr,
            "source":      "tg_alpha",
            "tg_channels": ",".join(sorted(rec["channels"])),
            "tg_mentions": rec["count"],
            "tg_snippet":  rec["snippet"],
        })

    # Sort by mention count — more mentions = more people calling it
    tokens.sort(key=lambda x: x["tg_mentions"], reverse=True)
    print(f"  TG alpha candidates: {len(tokens)} across {len(channels)} channels")
    return tokens
