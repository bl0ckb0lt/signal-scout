#!/usr/bin/env python3
"""
Lightweight position checker — runs every 5 min via cron-job.org.
Only checks open paper trades for SL/TP. Fast (~10 seconds).
No signal scanning, no heavy API calls.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scan_and_alert import (
    load_state, save_state, check_exits,
    tg_send, PAPER_MODE
)

try:
    from trader import check_real_exits, TRADE_MODE
except Exception:
    TRADE_MODE = "paper"
    def check_real_exits(*a, **k): pass


def main():
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat  = os.environ["TELEGRAM_CHAT_ID"]

    state = load_state()
    open_n = len(state.get("open", []))

    if not open_n:
        print("No open positions — nothing to check.")
        return

    print(f"Checking {open_n} open position(s)...")
    before = len(state.get("open", []))
    state  = check_exits(state, tg_token, tg_chat)
    after  = len(state.get("open", []))

    if TRADE_MODE != "paper":
        check_real_exits(tg_token, tg_chat)

    if before != after:
        save_state(state)
        print(f"  {before - after} position(s) closed.")
    else:
        print("  All positions still open.")


if __name__ == "__main__":
    main()
