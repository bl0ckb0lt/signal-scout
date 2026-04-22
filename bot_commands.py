"""
Telegram bot command handler for Signal Scout Premium.
Supports: /start /scan /top10 /status /pause /resume /threshold /watch /unwatch /watchlist /help
Uses long-polling (no webhook needed).
"""

import os
import json
import time
import threading
import subprocess
from pathlib import Path


# ── Telegram helpers ──────────────────────────────────────────────────────────

def tg_get(token: str, method: str, params: dict = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += "?" + qs
    r = subprocess.run(["curl", "-s", "--max-time", "35", url], capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8"))
    except Exception:
        return {}


def tg_send(token: str, chat_id: str, text: str, parse_mode: str = "HTML") -> bool:
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": parse_mode})
    r = subprocess.run([
        "curl", "-s", "-X", "POST",
        f"https://api.telegram.org/bot{token}/sendMessage",
        "-H", "Content-Type: application/json",
        "-d", payload
    ], capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8")).get("ok", False)
    except Exception:
        return False


# ── State ─────────────────────────────────────────────────────────────────────

STATE_FILE = Path(__file__).parent / ".bot_state.json"

DEFAULT_STATE = {
    "paused": False,
    "threshold": 55,
    "watchlist": [],
    "last_scan_time": None,
    "last_scan_count": 0,
    "total_alerts_sent": 0,
    "scan_number": 0,
}


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return {**DEFAULT_STATE, **json.loads(STATE_FILE.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return DEFAULT_STATE.copy()


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ── Command handlers ──────────────────────────────────────────────────────────

def handle_command(token: str, chat_id: str, text: str, state: dict,
                   trigger_scan_fn=None) -> dict:
    """Process a command and return updated state."""
    text = text.strip()
    cmd = text.split()[0].lower().split("@")[0]  # handle /cmd@botname format
    args = text.split()[1:] if len(text.split()) > 1 else []

    if cmd == "/start" or cmd == "/help":
        tg_send(token, chat_id,
            "🔍 <b>Signal Scout Premium</b>\n\n"
            "Real-time meme token scanner across Solana, ETH, BSC, Base &amp; X Layer.\n\n"
            "<b>Commands:</b>\n"
            "/scan — run a scan right now\n"
            "/top10 — show top 10 signals from last scan\n"
            "/status — monitor health &amp; stats\n"
            "/pause — pause alerts\n"
            "/resume — resume alerts\n"
            "/threshold 60 — set min score to alert (default 55)\n"
            "/watch SOL:ADDRESS — watch a specific token\n"
            "/unwatch SOL:ADDRESS — stop watching a token\n"
            "/watchlist — show your watched tokens\n"
            "/help — show this menu"
        )

    elif cmd == "/status":
        paused = "⏸ PAUSED" if state["paused"] else "▶️ RUNNING"
        last = state.get("last_scan_time") or "never"
        tg_send(token, chat_id,
            f"📊 <b>Signal Scout Status</b>\n\n"
            f"Status: {paused}\n"
            f"Alert threshold: {state['threshold']}/100\n"
            f"Scans completed: {state['scan_number']}\n"
            f"Total alerts sent: {state['total_alerts_sent']}\n"
            f"Last scan: {last}\n"
            f"Watching: {len(state['watchlist'])} token(s)"
        )

    elif cmd == "/pause":
        state["paused"] = True
        save_state(state)
        tg_send(token, chat_id, "⏸ Alerts paused. Send /resume to restart.")

    elif cmd == "/resume":
        state["paused"] = False
        save_state(state)
        tg_send(token, chat_id, "▶️ Alerts resumed. I'm watching the markets.")

    elif cmd == "/threshold":
        if args and args[0].isdigit():
            val = int(args[0])
            if 1 <= val <= 100:
                state["threshold"] = val
                save_state(state)
                tg_send(token, chat_id, f"✅ Alert threshold set to {val}/100.")
            else:
                tg_send(token, chat_id, "⚠️ Threshold must be between 1 and 100.")
        else:
            tg_send(token, chat_id,
                f"Current threshold: {state['threshold']}/100\n"
                "Usage: /threshold 60"
            )

    elif cmd == "/watch":
        if args:
            entry = args[0].upper()
            if entry not in state["watchlist"]:
                state["watchlist"].append(entry)
                save_state(state)
                tg_send(token, chat_id, f"👁 Watching <b>{entry}</b>. I'll alert on any score update.")
            else:
                tg_send(token, chat_id, f"Already watching {entry}.")
        else:
            tg_send(token, chat_id, "Usage: /watch SYMBOL\nExample: /watch MAGA")

    elif cmd == "/unwatch":
        if args:
            entry = args[0].upper()
            if entry in state["watchlist"]:
                state["watchlist"].remove(entry)
                save_state(state)
                tg_send(token, chat_id, f"✅ Removed {entry} from watchlist.")
            else:
                tg_send(token, chat_id, f"{entry} wasn't in your watchlist.")
        else:
            tg_send(token, chat_id, "Usage: /unwatch SYMBOL")

    elif cmd == "/watchlist":
        wl = state.get("watchlist", [])
        if wl:
            tg_send(token, chat_id, "👁 <b>Watchlist:</b>\n" + "\n".join(f"• {w}" for w in wl))
        else:
            tg_send(token, chat_id, "Your watchlist is empty. Use /watch SYMBOL to add tokens.")

    elif cmd == "/scan":
        tg_send(token, chat_id, "🔄 Running scan now... (takes ~30 seconds)")
        if trigger_scan_fn:
            threading.Thread(target=trigger_scan_fn, daemon=True).start()
        else:
            tg_send(token, chat_id, "⚠️ Scanner not available. Start monitor.py to enable /scan.")

    elif cmd == "/top10":
        results_file = Path(__file__).parent / "last_scan_results.json"
        if results_file.exists():
            try:
                scored = json.loads(results_file.read_text(encoding="utf-8"))
                top = [t for t in scored if t.get("verdict") != "AVOID"][:10]
                if not top:
                    tg_send(token, chat_id, "No strong signals in last scan.")
                else:
                    lines = ["📊 <b>Top 10 from last scan:</b>\n"]
                    for i, t in enumerate(top, 1):
                        v = t.get("verdict", "?")
                        emoji = {"BUY": "🟢", "WATCH": "🟡"}.get(v, "⚪")
                        pc1 = t.get("price_change_h1")
                        pc_str = f"{pc1:+.0f}%" if pc1 is not None else "N/A"
                        age = t.get("pair_age_hours")
                        age_str = f"{age:.1f}h" if age is not None else "?"
                        url = t.get("pair_url", "")
                        link = f" <a href='{url}'>chart</a>" if url else ""
                        lines.append(
                            f"{i}. {emoji} <b>{t.get('symbol','?')}</b> "
                            f"({t.get('chain','?').upper()}) "
                            f"Score:{t.get('score_total',0)} | 1h:{pc_str} | {age_str}{link}"
                        )
                    tg_send(token, chat_id, "\n".join(lines))
            except Exception as e:
                tg_send(token, chat_id, f"Error loading results: {e}")
        else:
            tg_send(token, chat_id, "No scan results yet. Use /scan to run one.")

    return state


# ── Long-poll listener ────────────────────────────────────────────────────────

def start_command_listener(token: str, chat_id: str, state: dict,
                            trigger_scan_fn=None) -> threading.Thread:
    """
    Start a background thread that polls Telegram for commands.
    Returns the thread (already started).
    """
    def _poll():
        offset = None
        print("[Bot] Command listener started.")
        while True:
            try:
                params = {"timeout": 30, "allowed_updates": ["message"]}
                if offset:
                    params["offset"] = offset
                resp = tg_get(token, "getUpdates", params)
                updates = resp.get("result", [])
                for update in updates:
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    msg_chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "")
                    if msg_chat_id == str(chat_id) and text.startswith("/"):
                        print(f"[Bot] Command: {text}")
                        updated = handle_command(
                            token, chat_id, text, state, trigger_scan_fn
                        )
                        state.update(updated)
            except Exception as e:
                print(f"[Bot] Poll error: {e}")
                time.sleep(5)

    t = threading.Thread(target=_poll, daemon=True)
    t.start()
    return t
