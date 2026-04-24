#!/usr/bin/env python3
"""
Signal Scout — instant command handler.
Runs every 1 min via cron-job.org → repository_dispatch.
Only processes Telegram commands, no full scan. Fast (~5 seconds).
"""
import os, json, subprocess, datetime, sys

# Safe imports — commands still work even if these fail
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from whales import whale_summary
except Exception as _e:
    def whale_summary(): return f"⚠️ Whale module unavailable: {_e}"

try:
    from trader import real_trade_summary, handle_approve, TRADE_MODE
except Exception as _e:
    TRADE_MODE = "paper"
    def real_trade_summary(): return f"⚠️ Trader module unavailable: {_e}"
    def handle_approve(sym, tok, chat): pass

PAPER_TRADES_FILE = "paper_trades.json"
STOP_LOSS_PCT     = 15.0
TRAIL_ACTIVATE_PCT= 15.0
TRAIL_PCT         = 10.0
HARD_TP_PCT       = 60.0


def curl(url, headers=None):
    args = ["curl", "-s", "--max-time", "10", url]
    for k, v in (headers or {}).items():
        args += ["-H", f"{k}: {v}"]
    r = subprocess.run(args, capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8"))
    except Exception:
        return None


def tg_send(token, chat_id, text):
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
    subprocess.run([
        "curl", "-s", "-X", "POST",
        f"https://api.telegram.org/bot{token}/sendMessage",
        "-H", "Content-Type: application/json", "-d", payload,
    ], capture_output=True)


def register_bot_menu(token):
    """
    Register the command list with Telegram so the '/' menu button
    shows all available commands inside the chat.
    Only needs to run once — safe to call every time (idempotent).
    """
    commands = [
        {"command": "status",   "description": "📊 P&L, win rate, risk settings"},
        {"command": "trades",   "description": "📋 Open positions + trailing stops"},
        {"command": "history",  "description": "📜 Last 10 closed trades"},
        {"command": "whales",   "description": "🐋 Tracked whale wallets"},
        {"command": "real",     "description": "💰 Real trade P&L (when enabled)"},
        {"command": "pause",    "description": "⏸ Stop sending alerts"},
        {"command": "resume",   "description": "▶️ Restart scanning"},
        {"command": "help",     "description": "🤖 Show all commands & settings"},
    ]
    payload = json.dumps({"commands": commands})
    subprocess.run([
        "curl", "-s", "-X", "POST",
        f"https://api.telegram.org/bot{token}/setMyCommands",
        "-H", "Content-Type: application/json", "-d", payload,
    ], capture_output=True)
    print("  Bot menu registered.")


def load_state():
    try:
        with open(PAPER_TRADES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_update_id": 0, "paused": False, "open": [], "closed": []}


def save_state(state):
    with open(PAPER_TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    subprocess.run(["git", "config", "user.email", "signalscout@bot"], capture_output=True)
    subprocess.run(["git", "config", "user.name",  "Signal Scout Bot"], capture_output=True)
    subprocess.run(["git", "add", PAPER_TRADES_FILE], capture_output=True)
    r = subprocess.run(["git", "commit", "-m", "chore: command state [skip ci]"], capture_output=True)
    if b"nothing to commit" not in r.stdout + r.stderr:
        # Sync with remote before pushing to avoid rejection from concurrent writes
        subprocess.run(["git", "pull", "--rebase", "--autostash"], capture_output=True)
        subprocess.run(["git", "push"], capture_output=True)


def handle_commands(tg_token, tg_chat, state):
    offset  = state.get("last_update_id", 0) + 1
    resp    = curl(f"https://api.telegram.org/bot{tg_token}/getUpdates?offset={offset}&limit=20&timeout=0") or {}
    updates = resp.get("result", [])

    changed = False
    for upd in updates:
        uid = upd.get("update_id")
        msg  = upd.get("message", {})
        text = (msg.get("text") or "").strip().lower().split("@")[0]
        cid  = str(msg.get("chat", {}).get("id", ""))

        # Always advance the offset pointer, even for non-chat messages
        if uid is not None:
            state["last_update_id"] = uid
            changed = True

        if cid != str(tg_chat):
            continue

        print(f"  Command: {text}")

        try:
            if text == "/pause":
                state["paused"] = True
                tg_send(tg_token, tg_chat, "⏸ <b>Signal Scout paused.</b>\nSend /resume to restart.")

            elif text == "/resume":
                state["paused"] = False
                tg_send(tg_token, tg_chat, "▶️ <b>Signal Scout resumed.</b>\nScanning every 5 min.")

            elif text == "/status":
                closed   = state.get("closed", [])
                wins     = [t for t in closed if t.get("status") in ("TP","TSL","HARD_TP")]
                losses   = [t for t in closed if t.get("status") == "SL"]
                total_c  = len(closed)
                avg_win  = sum(t.get("exit_pct",0) for t in wins)  / max(len(wins),1)
                avg_loss = sum(abs(t.get("exit_pct",0)) for t in losses) / max(len(losses),1)
                open_pos = state.get("open", [])
                trailing = sum(1 for t in open_pos if t.get("trailing_active"))
                tg_send(tg_token, tg_chat,
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"  📊 SIGNAL SCOUT STATUS\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"Mode: {'⏸ Paused' if state.get('paused') else '🟢 Active'}\n\n"
                    f"─── Open Positions ─────────\n"
                    f"  Total: {len(open_pos)}  ·  🔒 Trailing: {trailing}\n\n"
                    f"─── Closed Trades ──────────\n"
                    f"  ✅ Wins:  {len(wins)}  ·  ❌ Losses: {len(losses)}\n"
                    f"  Win rate:  {round(len(wins)/max(total_c,1)*100)}%\n"
                    f"  Avg win:   +{avg_win:.1f}%\n"
                    f"  Avg loss:  -{avg_loss:.1f}%\n\n"
                    f"─── Risk Settings ──────────\n"
                    f"  Fixed SL:      -{STOP_LOSS_PCT:.0f}%\n"
                    f"  Trail starts:  +{TRAIL_ACTIVATE_PCT:.0f}%\n"
                    f"  Trail gap:      {TRAIL_PCT:.0f}% from peak\n"
                    f"  Hard TP:       +{HARD_TP_PCT:.0f}%\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━"
                )

            elif text == "/trades":
                open_pos = state.get("open", [])
                if not open_pos:
                    tg_send(tg_token, tg_chat,
                        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "  📋 OPEN POSITIONS\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "No open paper trades right now."
                    )
                else:
                    lines = [
                        "━━━━━━━━━━━━━━━━━━━━━━━━━",
                        f"  📋 OPEN POSITIONS ({len(open_pos)})",
                        "━━━━━━━━━━━━━━━━━━━━━━━━━",
                    ]
                    for p in open_pos:
                        sym    = p.get("symbol", "?")
                        chain  = p.get("chain", "?").upper()
                        addr   = p.get("address", "")
                        score  = p.get("score", 0)
                        entry  = p.get("entry_price") or 0
                        peak   = p.get("peak_price") or entry
                        pct    = p.get("current_pct", 0)
                        trail  = peak * (1 - TRAIL_PCT/100) if p.get("trailing_active") else None
                        icon   = "🔒" if p.get("trailing_active") else "⏳"
                        lines.append(
                            f"\n{icon} <b>{sym}</b> ({chain})\n"
                            f"  Entry  ${entry:.8f}\n"
                            f"  P&L    {pct:+.1f}%  ·  Score {score}\n"
                            + (f"  Trail  ${trail:.8f}\n" if trail else "")
                            + f"  <code>{addr}</code>"
                        )
                    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━")
                    msg_text = "\n".join(lines)
                    # Telegram hard limit is 4096 chars
                    if len(msg_text) > 4000:
                        msg_text = msg_text[:4000] + "\n…(truncated)"
                    tg_send(tg_token, tg_chat, msg_text)

            elif text == "/history":
                closed = state.get("closed", [])
                if not closed:
                    tg_send(tg_token, tg_chat, "No closed trades yet.")
                else:
                    lines = [
                        "━━━━━━━━━━━━━━━━━━━━━━━━━",
                        f"  📜 TRADE HISTORY ({len(closed)})",
                        "━━━━━━━━━━━━━━━━━━━━━━━━━",
                    ]
                    for t in closed[-10:]:
                        icon = "✅" if t.get("status") in ("TP","TSL","HARD_TP") else "❌"
                        pct  = t.get("exit_pct", 0)
                        sym  = t.get("symbol", "?")
                        lines.append(
                            f"{icon} <b>{sym}</b> {pct:+.1f}%  [{t.get('status','')}]"
                        )
                    tg_send(tg_token, tg_chat, "\n".join(lines))

            elif text == "/whales":
                tg_send(tg_token, tg_chat, whale_summary())

            elif text == "/real":
                tg_send(tg_token, tg_chat, real_trade_summary())

            elif text.startswith("/approve "):
                sym = text.split(" ", 1)[1].strip().upper()
                handle_approve(sym, tg_token, tg_chat)

            elif text in ("/help", "/start"):
                tg_send(tg_token, tg_chat,
                    "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "  🤖 SIGNAL SCOUT v5\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "/status         — P&L, win rate, settings\n"
                    "/trades         — open positions + trail stops\n"
                    "/history        — last 10 closed trades\n"
                    "/whales         — tracked whale wallets\n"
                    "/real           — real trade P&L\n"
                    "/approve <sym>  — approve semi-auto buy\n"
                    "/pause          — stop alerts\n"
                    "/resume         — restart alerts\n\n"
                    "─── Risk Ladder ────────────\n"
                    f"  🛑 SL       -{STOP_LOSS_PCT:.0f}%\n"
                    f"  🔒 Trail   +{TRAIL_ACTIVATE_PCT:.0f}% ({TRAIL_PCT:.0f}% from peak)\n"
                    f"  🚀 Hard TP +{HARD_TP_PCT:.0f}%\n\n"
                    f"─── Trade Mode ─────────────\n"
                    f"  {TRADE_MODE.upper()}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━"
                )

        except Exception as _cmd_err:
            print(f"  ERROR handling '{text}': {_cmd_err}")
            try:
                tg_send(tg_token, tg_chat, f"⚠️ Command failed: <code>{text}</code>")
            except Exception:
                pass

    return state, changed


def main():
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat  = os.environ["TELEGRAM_CHAT_ID"]

    print("Command handler starting...")

    # Register the Telegram '/' menu on every run (idempotent, ~50ms)
    register_bot_menu(tg_token)

    state = load_state()
    state, changed = handle_commands(tg_token, tg_chat, state)

    if changed:
        save_state(state)
        print("State saved.")
    else:
        print("No new commands.")


if __name__ == "__main__":
    main()
