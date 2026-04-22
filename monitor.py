#!/usr/bin/env python3
"""
Signal Scout Monitor — Live continuous token scanner with Telegram alerts.

Runs on a loop, scans for new tokens every N minutes, and fires Telegram
alerts when early tokens (< 2h old) score above the threshold.
"""

import os
import sys
import json
import time
import datetime
import argparse
from pathlib import Path

# Load .env
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from fetcher import scan_all_signals
from scorer import score_tokens
from telegram_bot import send_message, format_alert, format_scan_summary


# ── Config ───────────────────────────────────────────────────────────────────

SCAN_INTERVAL_MINUTES = 5       # how often to scan
ALERT_SCORE_THRESHOLD = 55      # minimum score to send alert
EARLY_TOKEN_MAX_HOURS = 3       # tokens younger than this are "early"
MAX_TOKENS_TO_SCORE = 30        # cap scoring per scan to save time
SEEN_TOKENS_FILE = Path(__file__).parent / ".seen_tokens.json"


# ── State ─────────────────────────────────────────────────────────────────────

def load_seen() -> dict:
    """Load previously alerted tokens {address: first_seen_timestamp}."""
    if SEEN_TOKENS_FILE.exists():
        try:
            return json.loads(SEEN_TOKENS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_seen(seen: dict):
    SEEN_TOKENS_FILE.write_text(json.dumps(seen, indent=2), encoding="utf-8")


def prune_seen(seen: dict, max_age_hours: int = 48) -> dict:
    """Remove tokens seen more than max_age_hours ago to keep the file small."""
    cutoff = time.time() - max_age_hours * 3600
    return {k: v for k, v in seen.items() if v > cutoff}


# ── Core loop ─────────────────────────────────────────────────────────────────

def run_monitor(
    tg_token: str,
    tg_chat_id: str,
    okx_key: str,
    okx_secret: str,
    okx_pass: str,
    score_threshold: int = ALERT_SCORE_THRESHOLD,
    scan_interval: int = SCAN_INTERVAL_MINUTES,
    early_hours: float = EARLY_TOKEN_MAX_HOURS,
    dry_run: bool = False,
):
    seen = load_seen()
    scan_number = 0

    print(f"\n{'='*50}")
    print(f"  Signal Scout Monitor — LIVE")
    print(f"  Scan every {scan_interval} min | Alert threshold: {score_threshold}/100")
    print(f"  Early token window: < {early_hours}h old")
    print(f"  Telegram: {'DRY RUN (no alerts)' if dry_run else 'ACTIVE'}")
    print(f"{'='*50}\n")

    # Send startup message
    if not dry_run:
        send_message(tg_token, tg_chat_id,
            f"🚀 <b>Signal Scout Monitor started</b>\n\n"
            f"Scanning every {scan_interval} minutes\n"
            f"Alert threshold: {score_threshold}/100\n"
            f"Early token window: under {early_hours}h old\n\n"
            f"I'll alert you the moment I find a strong early signal."
        )

    while True:
        scan_number += 1
        now_str = datetime.datetime.utcnow().strftime("%H:%M UTC")
        print(f"\n[{now_str}] Scan #{scan_number} starting...")

        try:
            # 1. Fetch tokens
            tokens = scan_all_signals(okx_key, okx_secret, okx_pass)
            print(f"  Fetched {len(tokens)} tokens")

            # 2. Filter: only tokens with enough liquidity
            viable = [t for t in tokens if (t.get("liquidity_usd") or 0) > 5000]

            # 3. Score them
            scored = score_tokens(viable, top_n=MAX_TOKENS_TO_SCORE)
            print(f"  Scored {len(scored)} tokens")

            # 4. Find new early high-scorers
            new_alerts = []
            for t in scored:
                addr = t.get("address", "")
                chain = t.get("chain", "")
                key = f"{chain}:{addr}"
                age = t.get("pair_age_hours")
                score = t.get("score_total", 0)
                verdict = t.get("verdict", "AVOID")

                is_early = age is not None and age <= early_hours
                is_strong = score >= score_threshold
                is_new = key not in seen
                not_avoid = verdict != "AVOID"

                if is_strong and not_avoid and is_new:
                    new_alerts.append(t)
                    seen[key] = time.time()
                    print(f"  *** NEW ALERT: {t.get('symbol')} score={score} age={age}h verdict={verdict}")

            # 5. Send individual alerts for new tokens
            for t in new_alerts:
                is_early = (t.get("pair_age_hours") or 99) <= early_hours
                msg = format_alert(t, is_new=is_early)
                print(f"  Sending Telegram alert for {t.get('symbol')}...")
                if not dry_run:
                    ok = send_message(tg_token, tg_chat_id, msg)
                    if not ok:
                        print(f"  WARNING: Telegram send failed for {t.get('symbol')}")
                    time.sleep(1)  # avoid hitting TG rate limit

            # 6. Send scan summary every 6 scans (every ~30 min)
            if scan_number % 6 == 0 and scored:
                summary = format_scan_summary(scored, scan_number)
                print(f"  Sending scan summary...")
                if not dry_run:
                    send_message(tg_token, tg_chat_id, summary)

            if not new_alerts:
                print(f"  No new alerts this scan. Top score: "
                      f"{scored[0].get('score_total',0) if scored else 0}/100 "
                      f"({scored[0].get('symbol','?') if scored else 'none'})")

            # 7. Prune old seen tokens
            seen = prune_seen(seen)
            save_seen(seen)

        except KeyboardInterrupt:
            print("\nMonitor stopped by user.")
            if not dry_run:
                send_message(tg_token, tg_chat_id, "⏹ Signal Scout Monitor stopped.")
            break
        except Exception as e:
            print(f"  ERROR during scan: {e}")
            if not dry_run:
                send_message(tg_token, tg_chat_id, f"⚠️ Scan error: {e}")

        # 8. Wait for next scan
        print(f"  Next scan in {scan_interval} minutes...")
        time.sleep(scan_interval * 60)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Signal Scout live monitor with Telegram alerts")
    parser.add_argument("--interval", type=int, default=SCAN_INTERVAL_MINUTES,
                        help=f"Scan interval in minutes (default: {SCAN_INTERVAL_MINUTES})")
    parser.add_argument("--threshold", type=int, default=ALERT_SCORE_THRESHOLD,
                        help=f"Minimum score to alert (default: {ALERT_SCORE_THRESHOLD})")
    parser.add_argument("--early-hours", type=float, default=EARLY_TOKEN_MAX_HOURS,
                        help=f"Max token age in hours to be 'early' (default: {EARLY_TOKEN_MAX_HOURS})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without sending Telegram messages")
    args = parser.parse_args()

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not args.dry_run:
        if not tg_token or tg_token == "your_telegram_bot_token":
            print("ERROR: Set TELEGRAM_BOT_TOKEN in your .env file")
            print("  1. Message @BotFather on Telegram")
            print("  2. Send /newbot and follow the steps")
            print("  3. Copy the token into .env")
            sys.exit(1)
        if not tg_chat_id or tg_chat_id == "your_telegram_chat_id":
            print("ERROR: Set TELEGRAM_CHAT_ID in your .env file")
            print("  Message @userinfobot on Telegram to get your Chat ID")
            sys.exit(1)

    run_monitor(
        tg_token=tg_token,
        tg_chat_id=tg_chat_id,
        okx_key=os.environ.get("OKX_API_KEY", ""),
        okx_secret=os.environ.get("OKX_SECRET_KEY", ""),
        okx_pass=os.environ.get("OKX_PASSPHRASE", ""),
        score_threshold=args.threshold,
        scan_interval=args.interval,
        early_hours=args.early_hours,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
