#!/usr/bin/env python3
"""
Signal Scout Premium Monitor
Live multi-chain meme token scanner with Telegram alerts + bot commands.

Features:
- Scans every N minutes across Solana, ETH, BSC, Base, X Layer
- Rug/honeypot detection before alerting
- Telegram commands: /scan /top10 /status /pause /resume /threshold /watch
- Watchlist tracking for specific tokens
- Railway/VPS ready (no GUI needed)
"""

import os
import sys
import json
import time
import datetime
import argparse
import threading
from pathlib import Path

# Load .env (works locally; on Railway use env vars directly)
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from fetcher import scan_all_signals
from scorer import score_tokens
from rugcheck import check_token, is_safe, rug_summary
from telegram_bot import send_message, format_alert, format_scan_summary
from bot_commands import load_state, save_state, start_command_listener


SEEN_FILE     = Path(__file__).parent / ".seen_tokens.json"
RESULTS_FILE  = Path(__file__).parent / "last_scan_results.json"


# ── Seen token tracking ───────────────────────────────────────────────────────

def load_seen() -> dict:
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_seen(seen: dict):
    SEEN_FILE.write_text(json.dumps(seen, indent=2), encoding="utf-8")


def prune_seen(seen: dict, max_hours: int = 48) -> dict:
    cutoff = time.time() - max_hours * 3600
    return {k: v for k, v in seen.items() if v > cutoff}


# ── Single scan ───────────────────────────────────────────────────────────────

def run_one_scan(creds: dict, state: dict, seen: dict,
                 tg_token: str, tg_chat_id: str,
                 early_hours: float = 3.0) -> tuple[dict, dict]:
    """
    Run a full scan cycle. Returns (updated_state, updated_seen).
    """
    state["scan_number"] = state.get("scan_number", 0) + 1
    state["last_scan_time"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    now_str = state["last_scan_time"]
    scan_n = state["scan_number"]

    print(f"\n[{now_str}] Scan #{scan_n} starting...")

    # 1. Fetch
    tokens = scan_all_signals(
        creds["okx_api_key"], creds["okx_secret"], creds["okx_passphrase"]
    )
    viable = [t for t in tokens if (t.get("liquidity_usd") or 0) > 5000]
    print(f"  Fetched {len(tokens)} tokens, {len(viable)} viable")

    # 2. Score
    scored = score_tokens(viable, top_n=30)
    state["last_scan_count"] = len(scored)

    # 3. Rug-check top candidates before alerting
    print(f"  Running rug checks...")
    threshold = state.get("threshold", 55)
    candidates = [t for t in scored if t.get("score_total", 0) >= threshold
                  and t.get("verdict") != "AVOID"]
    for t in candidates:
        check_token(t)   # enriches in-place via dict reference won't work, do it properly:
    candidates = [check_token(t) for t in candidates]

    # 4. Save results for /top10
    RESULTS_FILE.write_text(
        json.dumps(scored, indent=2, default=str), encoding="utf-8"
    )

    # 5. Alert on new high-scoring tokens
    new_alerts = 0
    watchlist = [w.upper() for w in state.get("watchlist", [])]

    for t in scored:
        addr    = t.get("address", "")
        chain   = t.get("chain", "")
        symbol  = (t.get("symbol") or "").upper()
        key     = f"{chain}:{addr}"
        age     = t.get("pair_age_hours")
        score   = t.get("score_total", 0)
        verdict = t.get("verdict", "AVOID")

        on_watchlist = symbol in watchlist
        is_strong    = score >= threshold
        is_new       = key not in seen
        not_avoid    = verdict != "AVOID"
        not_rugged   = is_safe(t)

        should_alert = not_avoid and is_new and not_rugged and (is_strong or on_watchlist)

        if should_alert and not state.get("paused"):
            is_early = age is not None and age <= early_hours
            msg = format_alert(t, is_new=is_early)

            # Append rug check line
            rug_line = rug_summary(t)
            msg += f"\n\n🛡 {rug_line}"

            if on_watchlist:
                msg = "👁 <b>WATCHLIST ALERT</b>\n\n" + msg

            ok = send_message(tg_token, tg_chat_id, msg)
            if ok:
                seen[key] = time.time()
                new_alerts += 1
                state["total_alerts_sent"] = state.get("total_alerts_sent", 0) + 1
                print(f"  ✅ Alert sent: {symbol} score={score} age={age}h rug={rug_line}")
                time.sleep(1)
            else:
                print(f"  ⚠️  Telegram send failed for {symbol}")

        elif is_new and is_strong and not not_rugged:
            print(f"  🚨 SKIPPED (rugged): {symbol} — {rug_summary(t)}")
            seen[key] = time.time()  # mark seen so we don't keep checking

    if new_alerts == 0:
        top = scored[0] if scored else {}
        print(f"  No new alerts. Top: {top.get('symbol','?')} {top.get('score_total',0)}/100")

    # 6. Summary every 6 scans (~30 min)
    if scan_n % 6 == 0 and scored and not state.get("paused"):
        summary = format_scan_summary(scored, scan_n)
        send_message(tg_token, tg_chat_id, summary)

    seen = prune_seen(seen)
    save_seen(seen)
    save_state(state)

    return state, seen


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Signal Scout Premium Monitor")
    parser.add_argument("--interval",    type=int,   default=5,   help="Scan interval minutes")
    parser.add_argument("--threshold",   type=int,   default=55,  help="Alert score threshold")
    parser.add_argument("--early-hours", type=float, default=3.0, help="Max age for early alert")
    parser.add_argument("--dry-run",     action="store_true",      help="No Telegram messages")
    args = parser.parse_args()

    # Credentials
    tg_token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    creds = {
        "okx_api_key":    os.environ["OKX_API_KEY"],
        "okx_secret":     os.environ["OKX_SECRET_KEY"],
        "okx_passphrase": os.environ["OKX_PASSPHRASE"],
    }

    if not args.dry_run and (not tg_token or not tg_chat_id):
        print("ERROR: Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        sys.exit(1)

    # State
    state = load_state()
    state["threshold"] = args.threshold
    seen  = load_seen()

    print(f"\n{'='*52}")
    print(f"  Signal Scout Premium — LIVE")
    print(f"  Scan every {args.interval} min | Threshold: {args.threshold}/100")
    print(f"  Early window: <{args.early_hours}h | Rug check: ON")
    print(f"  Commands: /scan /top10 /status /pause /resume")
    print(f"  Telegram: {'DRY RUN' if args.dry_run else 'ACTIVE'}")
    print(f"{'='*52}\n")

    # Scan trigger for /scan command
    scan_lock = threading.Lock()
    def trigger_scan():
        with scan_lock:
            nonlocal state, seen
            state, seen = run_one_scan(
                creds, state, seen, tg_token, tg_chat_id, args.early_hours
            )

    # Start command listener
    if not args.dry_run:
        start_command_listener(tg_token, tg_chat_id, state, trigger_scan)

    # Startup message
    if not args.dry_run:
        send_message(tg_token, tg_chat_id,
            f"🚀 <b>Signal Scout Premium — LIVE</b>\n\n"
            f"Scanning every {args.interval} min\n"
            f"Alert threshold: {args.threshold}/100\n"
            f"Early token window: &lt;{args.early_hours}h old\n"
            f"Rug/honeypot detection: ON\n\n"
            f"Commands: /scan /top10 /status /pause /resume /threshold /watch /help\n\n"
            f"First scan starting now..."
        )

    # Main loop
    while True:
        try:
            state, seen = run_one_scan(
                creds, state, seen, tg_token, tg_chat_id, args.early_hours
            )
        except KeyboardInterrupt:
            print("\nStopped.")
            if not args.dry_run:
                send_message(tg_token, tg_chat_id, "⏹ Signal Scout stopped.")
            break
        except Exception as e:
            print(f"Scan error: {e}")
            if not args.dry_run:
                send_message(tg_token, tg_chat_id, f"⚠️ Scan error: {e}")

        print(f"  Sleeping {args.interval} min...")
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
