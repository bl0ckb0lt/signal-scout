#!/usr/bin/env python3
"""
Signal Scout v5 — GitHub Actions runner.
+ Trailing stop loss  (locks in gains as price rises)
+ Hard take-profit cap
+ Fixed stop loss before trailing activates
+ Paper trading, trade logger, bot commands, token images
+ Whale tracking (Helius API) with 80%+ win-rate wallets
+ Jupiter auto-trading (paper / semi / auto mode)
Runs every 5 min via GitHub Actions cron. No server needed.
"""

import os, json, time, datetime, subprocess

# ── Config ────────────────────────────────────────────────────────────────────

MAX_AGE_MINUTES      = 120   # tighter — fresher tokens only
MIN_SCORE            = 70   # raised from 65 — 65-68 range had consistent losses
WHALE_MIN_SCORE      = 58   # whale/smart override still needs minimum quality
STRONG_BUY_SCORE     = 80   # premium tier shown in alert card
EARLY_SL_MINUTES     = 20   # tighter SL window after entry (meme rugs happen fast)
EARLY_SL_PCT         = 8.0  # tightened from 10% — catch dumps faster in early window
NO_BOUNCE_AGE_MIN    = 5    # reduced from 10 — meme crashes happen in 2-3 min not 10
NO_BOUNCE_DOWN_PCT   = 5.0  # reduced from 7% — exit sooner on straight dump
NO_BOUNCE_PEAK_PCT   = 4.0  # max peak gain to qualify as "no bounce" (straight dump)
MIN_LIQUIDITY        = 25000 # raised from 5K — low liq = easy dump
MAX_FDV              = 5_000_000  # skip if market cap already >$5M
MAX_TOKENS           = 20   # fewer, higher quality
MIN_MOMENTUM_H1      = 20   # raised from 15%
MIN_MOMENTUM_M5      = -3.0 # 5m change must not be actively dumping at entry

PUMP_MIN_MCAP        = 10_000
PUMP_MAX_PROGRESS    = 80
PUMP_MIN_PROGRESS    = 5
PUMP_MAX_AGE_MINUTES = 90
PUMP_MIN_TRADES      = 15   # more trades = real interest

# ── Paper trading ─────────────────────────────────────────────────────────────

PAPER_MODE          = True
STOP_LOSS_PCT       = 15.0
TRAIL_ACTIVATE_PCT  = 15.0
TRAIL_PCT           = 10.0
HARD_TP_PCT         = 250.0  # raised from 60% — trailing stop is the real exit, hard cap only kills moonshots early
MAX_OPEN_TRADES     = 8
PAPER_TRADES_FILE   = "paper_trades.json"
MAX_SL_FAILURES     = 3     # alert after N consecutive API failures on a position
MAX_STALE_FAILURES  = 24    # force-close zombie position after ~2h of dead feed
MIN_VOLUME_24H      = 50_000  # min 24h volume — filters low-conviction entries
VOL_DECAY_THRESHOLD = 0.15   # exit if 5m vol < 15% of peak 5m vol seen since entry
VOL_DECAY_MIN_PROFIT= 5.0    # only trigger volume decay exit if position is +5%+
VOL_DECAY_MIN_AGE   = 15     # position must be at least 15 min old before vol decay fires

# ── Sniper milestones — Telegram alerts at key profit levels (no forced exit) ─
MILESTONE_PCTS      = [50, 100, 200]  # alert at these % gains so you can decide manually

# ── Smart money wallets (mirrors VERIFIED_WHALES in whales.py) ───────────────

try:
    from whales import VERIFIED_WHALES, get_whale_buys, get_whale_exits, whale_summary
except Exception as _we:
    print(f"whales import failed: {_we}")
    VERIFIED_WHALES = {}
    def get_whale_buys(*a, **k): return []
    def get_whale_exits(*a, **k): return set()
    def whale_summary(): return "⚠️ Whale module unavailable."

try:
    from trader import maybe_trade, check_real_exits, real_trade_summary, handle_approve, TRADE_MODE
except Exception as _te:
    print(f"trader import failed: {_te}")
    TRADE_MODE = "paper"
    def maybe_trade(*a, **k): pass
    def check_real_exits(*a, **k): pass
    def real_trade_summary(): return "⚠️ Trader module unavailable."
    def handle_approve(*a, **k): pass

try:
    from sheets_logger import sheets_log_open, sheets_log_close
except Exception as _se:
    print(f"sheets_logger import failed: {_se}")
    def sheets_log_open(*a, **k): pass
    def sheets_log_close(*a, **k): pass

try:
    from twitter_alerts import post_tweet
except Exception as _te:
    print(f"twitter_alerts import failed: {_te}")
    def post_tweet(*a, **k): pass

try:
    from twitter_search import fetch_x_tokens
except Exception as _xs:
    print(f"twitter_search import failed: {_xs}")
    def fetch_x_tokens(): return []

try:
    from tg_alpha import fetch_tg_alpha_tokens
except Exception as _ta:
    print(f"tg_alpha import failed: {_ta}")
    def fetch_tg_alpha_tokens(): return []

try:
    from gmgn import fetch_gmgn_tokens
except Exception as _gm:
    print(f"gmgn import failed: {_gm}")
    def fetch_gmgn_tokens(): return []

try:
    from birdeye import fetch_birdeye_tokens
except Exception as _be:
    print(f"birdeye import failed: {_be}")
    def fetch_birdeye_tokens(): return []

SMART_WALLETS = {addr: info["label"] for addr, info in VERIFIED_WHALES.items()}

# ── Helpers ───────────────────────────────────────────────────────────────────

def curl(url, headers=None, method="GET", body=None, timeout=15):
    args = ["curl", "-s", "--max-time", str(timeout), url]
    for k, v in (headers or {}).items():
        args += ["-H", f"{k}: {v}"]
    if body:
        args += ["-X", method, "-d", body]
    r = subprocess.run(args, capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8"))
    except Exception:
        return None


def tg_send(token, chat_id, text):
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
    r = subprocess.run([
        "curl", "-s", "-X", "POST",
        f"https://api.telegram.org/bot{token}/sendMessage",
        "-H", "Content-Type: application/json", "-d", payload,
    ], capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8")).get("ok", False)
    except Exception:
        return False


def tg_send_photo(token, chat_id, photo_url, caption):
    payload = json.dumps({
        "chat_id": chat_id, "photo": photo_url,
        "caption": caption, "parse_mode": "HTML",
    })
    r = subprocess.run([
        "curl", "-s", "-X", "POST",
        f"https://api.telegram.org/bot{token}/sendPhoto",
        "-H", "Content-Type: application/json", "-d", payload,
    ], capture_output=True)
    try:
        if json.loads(r.stdout.decode("utf-8")).get("ok"):
            return True
    except Exception:
        pass
    return tg_send(token, chat_id, caption)


# ── Telegram bot commands (stateless poll) ────────────────────────────────────

def poll_commands(tg_token, tg_chat, state):
    """
    Reads unprocessed messages from the bot chat.
    Returns updated state dict with keys: paused, last_update_id
    Handles: /status /trades /pause /resume
    """
    offset = state.get("last_update_id", 0) + 1
    url    = f"https://api.telegram.org/bot{tg_token}/getUpdates?offset={offset}&limit=20&timeout=0"
    resp   = curl(url) or {}
    updates = resp.get("result", [])

    for upd in updates:
        state["last_update_id"] = upd["update_id"]
        msg  = upd.get("message", {})
        text = (msg.get("text") or "").strip().lower()
        cid  = str(msg.get("chat", {}).get("id", ""))

        if cid != str(tg_chat):
            continue

        if text in ("/pause", "/pause@signalscoutbot"):
            state["paused"] = True
            tg_send(tg_token, tg_chat, "⏸ <b>Signal Scout paused.</b> Send /resume to restart.")

        elif text in ("/resume", "/resume@signalscoutbot"):
            state["paused"] = False
            tg_send(tg_token, tg_chat, "▶️ <b>Signal Scout resumed.</b> Scanning every 30 min.")

        elif text in ("/status", "/status@signalscoutbot"):
            open_n   = len(state.get("open", []))
            closed   = state.get("closed", [])
            closed_n = len(closed)
            wins     = sum(1 for t in closed if t.get("status") in ("TP", "TSL", "HARD_TP"))
            losses   = sum(1 for t in closed if t.get("status") == "SL")
            avg_win  = (sum(t.get("exit_pct", 0) for t in closed if t.get("exit_pct", 0) > 0)
                        / max(wins, 1))
            avg_loss = (sum(abs(t.get("exit_pct", 0)) for t in closed if t.get("exit_pct", 0) < 0)
                        / max(losses, 1))
            trailing = sum(1 for t in state.get("open", []) if t.get("trailing_active"))
            tg_send(tg_token, tg_chat,
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  📊 SIGNAL SCOUT STATUS\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Mode: {'⏸ Paused' if state.get('paused') else '🟢 Active'}\n\n"
                f"─── Open Positions ─────────\n"
                f"  Total: {open_n}  ·  🔒 Trailing: {trailing}\n\n"
                f"─── Closed Trades ──────────\n"
                f"  ✅ Winners: {wins}  ·  ❌ Losers: {losses}\n"
                f"  Win rate:  {round(wins/max(closed_n,1)*100)}%\n"
                f"  Avg win:   +{avg_win:.1f}%\n"
                f"  Avg loss:  -{avg_loss:.1f}%\n\n"
                f"─── Risk Settings ──────────\n"
                f"  Fixed SL:      -{STOP_LOSS_PCT:.0f}%\n"
                f"  Trail starts:  +{TRAIL_ACTIVATE_PCT:.0f}%\n"
                f"  Trail gap:     {TRAIL_PCT:.0f}% from peak\n"
                f"  Hard TP:       +{HARD_TP_PCT:.0f}%\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━"
            )

        elif text in ("/trades", "/trades@signalscoutbot"):
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
                    sym     = p.get("symbol", "?")
                    chain   = p.get("chain", "?").upper()
                    addr    = p.get("address", "")
                    score   = p.get("score", 0)
                    entry   = p.get("entry_price") or 0
                    peak    = p.get("peak_price") or entry
                    pct_now = p.get("current_pct", 0)
                    trail   = peak * (1 - TRAIL_PCT / 100) if p.get("trailing_active") else None
                    t_icon  = "🔒" if p.get("trailing_active") else "⏳"
                    lines.append(
                        f"\n{t_icon} <b>{sym}</b> ({chain})\n"
                        f"  Entry  ${entry:.8f}\n"
                        f"  Now    {pct_now:+.1f}%  ·  Score {score}\n"
                        + (f"  Trail SL  ${trail:.8f}\n" if trail else "")
                        + f"  <code>{addr}</code>"
                    )
                lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━")
                tg_send(tg_token, tg_chat, "\n".join(lines))

        elif text in ("/whales", "/whales@signalscoutbot"):
            tg_send(tg_token, tg_chat, whale_summary())

        elif text in ("/real", "/real@signalscoutbot"):
            tg_send(tg_token, tg_chat, real_trade_summary())

        elif text.startswith("/approve "):
            sym = text.split(" ", 1)[1].strip().upper()
            handle_approve(sym, tg_token, tg_chat)

        elif text in ("/help", "/start"):
            tg_send(tg_token, tg_chat,
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "  🤖 SIGNAL SCOUT v5\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "/status   — P&L, win rate, risk settings\n"
                "/trades   — open positions + trail stops\n"
                "/whales   — tracked whale wallets\n"
                "/real     — real trade P&L (if enabled)\n"
                "/pause    — stop sending alerts\n"
                "/resume   — restart scanning\n\n"
                "─── Risk Management ────────\n"
                f"  Fixed SL:     -{STOP_LOSS_PCT:.0f}%\n"
                f"  Trail starts: +{TRAIL_ACTIVATE_PCT:.0f}%\n"
                f"  Trail gap:     {TRAIL_PCT:.0f}% from peak\n"
                f"  Hard TP:      +{HARD_TP_PCT:.0f}%\n\n"
                f"─── Trade Mode ─────────────\n"
                f"  {TRADE_MODE.upper()}\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━"
            )

    return state


# ── Paper trade persistence ───────────────────────────────────────────────────

def load_state():
    try:
        with open(PAPER_TRADES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_update_id": 0, "paused": False, "open": [], "closed": []}


def save_state(state):
    with open(PAPER_TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    # Commit back so the file persists across stateless runs
    subprocess.run(["git", "config", "user.email", "signalscout@bot"], capture_output=True)
    subprocess.run(["git", "config", "user.name",  "Signal Scout Bot"], capture_output=True)
    subprocess.run(["git", "add", PAPER_TRADES_FILE], capture_output=True)
    r = subprocess.run(["git", "commit", "-m", "chore: update paper trades [skip ci]"],
                       capture_output=True)
    if b"nothing to commit" not in r.stdout + r.stderr:
        subprocess.run(["git", "push"], capture_output=True)


def _close_pos(pos, now_p, status, state):
    """Move position from open → closed with exit data."""
    entry = pos.get("entry_price") or 1
    pct   = round((now_p - entry) / entry * 100, 2)
    now   = datetime.datetime.now(datetime.UTC).isoformat()
    pos.update({"exit_price": now_p, "exit_pct": pct,
                "status": status, "exit_time": now})
    state.setdefault("closed", []).append(pos)
    state.setdefault("cooldown", {})[pos["address"]] = now
    sheets_log_close(pos)


def check_exits(state, tg_token, tg_chat):
    """
    Trailing stop engine — runs every 5 min scan.

    Flow per position:
      ① Price rises → update peak_price
      ② Once up TRAIL_ACTIVATE_PCT → trailing_active flips True (sticky, never resets)
      ③ While trailing: stop = peak × (1 - trail_gap/100)
         Gap tightens as peak gain grows: 10% → 7% → 5%
         If price drops below stop → exit (TSL hit)
      ④ Hard cap: if up HARD_TP_PCT → exit immediately (HARD_TP)
      ⑤ Fixed SL only applies before trailing ever activates
    """
    still_open = []

    for pos in state.get("open", []):
        addr        = pos["address"]
        chain       = pos["chain"]
        src         = pos.get("source", "")
        entry       = pos.get("entry_price") or 0
        sym         = pos["symbol"]

        # Skip pump.fun / xlayer (no reliable price feed)
        if not entry or chain == "xlayer" or src == "pump.fun":
            still_open.append(pos)
            continue

        # ── Conviction tier — high-quality tokens get more room to breathe ──
        # Tokens that checked every box shouldn't be cut on a normal dip.
        # Whale buys, multi-source confirms, and strong-buy scores get wider stops.
        pos_score      = pos.get("score", 0)
        src_count      = pos.get("source_count", 1)
        high_conviction = (
            pos_score >= STRONG_BUY_SCORE              # scored 80+
            or src in ("whale_buy", "tg_alpha", "x_alpha", "graduated")
            or src_count >= 2                          # seen in 2+ independent feeds
        )
        # High conviction: wider early SL, slower no-bounce trigger
        eff_early_sl_pct    = 12.0  if high_conviction else EARLY_SL_PCT       # 12% vs 8%
        eff_no_bounce_age   = 12    if high_conviction else NO_BOUNCE_AGE_MIN   # 12 min vs 5
        eff_no_bounce_down  = 10.0  if high_conviction else NO_BOUNCE_DOWN_PCT  # 10% vs 5%

        # ── Current price — DexScreener + Jupiter fallback ────────────────
        data  = curl(f"https://api.dexscreener.com/latest/dex/tokens/{addr}") or {}
        pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == chain]
        jup_price_used = False
        if not pairs and chain == "solana":
            # DexScreener missed it — try Jupiter (covers every Solana DEX, no key needed)
            jup = curl(f"https://api.jup.ag/price/v2?ids={addr}") or {}
            jup_p = float((jup.get("data", {}).get(addr, {}) or {}).get("price") or 0)
            if jup_p:
                now_p          = jup_p
                jup_price_used = True
                pos["api_failures"] = 0
                print(f"  Jupiter fallback: {sym} ${jup_p:.8f}")
        if not pairs and not jup_price_used:
            fails = pos.get("api_failures", 0) + 1
            pos["api_failures"] = fails
            if fails >= MAX_STALE_FAILURES:
                tg_send(tg_token, tg_chat,
                    f"🪦 <b>{sym}</b> — price feed dead {fails} scans (~{fails*5//60}h).\n"
                    f"Auto-closing as STALE — check manually.\n"
                    f"📋 <code>{addr}</code>")
                # Use last tracked price, not 0 — prevents false -100% records
                stale_price = entry * (1 + (pos.get("current_pct") or 0) / 100)
                _close_pos(pos, stale_price, "STALE", state)
                continue
            if fails >= MAX_SL_FAILURES:
                tg_send(tg_token, tg_chat,
                    f"⚠️ <b>{sym}</b> — price feed down {fails} scans in a row.\n"
                    f"Check manually: <code>{addr}</code>")
            still_open.append(pos)
            continue
        if not jup_price_used:
            p     = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
            now_p = float(p.get("priceUsd") or 0)
            if not now_p:
                pos["api_failures"] = pos.get("api_failures", 0) + 1
                still_open.append(pos)
                continue
        pos["api_failures"] = 0  # reset on successful price fetch

        pct_entry     = (now_p - entry) / entry * 100               # % from entry (current)
        peak          = max(pos.get("peak_price") or entry, now_p)  # all-time high
        pct_peak_gain = (peak - entry) / entry * 100                # best gain reached

        # Age of position (needed for multiple exit checks below)
        try:
            entry_dt = datetime.datetime.fromisoformat(pos.get("entry_time", ""))
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=datetime.UTC)
            age_min = (datetime.datetime.now(datetime.UTC) - entry_dt).total_seconds() / 60
        except Exception:
            age_min = EARLY_SL_MINUTES + 1

        # Tiered gap: tighten as the position has risen higher (use peak gain, not current)
        if pct_peak_gain >= 45:
            trail_gap = 5.0
        elif pct_peak_gain >= 30:
            trail_gap = 7.0
        else:
            trail_gap = TRAIL_PCT   # 10%
        trail_stop = peak * (1 - trail_gap / 100)

        was_trailing    = pos.get("trailing_active", False)
        # Sticky: once trailing activates it never turns off — prevents the
        # bug where a price pullback below TRAIL_ACTIVATE_PCT disables the
        # trailing guard and exposes the position to the full fixed SL again.
        trailing_active = was_trailing or (pct_entry >= TRAIL_ACTIVATE_PCT)

        # Track peak 5m volume — used for volume decay exit (skip if Jupiter fallback, no vol data)
        vol_m5      = float((p.get("volume") or {}).get("m5") or 0) if not jup_price_used else 0
        peak_vol_m5 = max(pos.get("peak_volume_m5") or 0, vol_m5)

        pos["peak_price"]      = peak
        pos["trailing_active"] = trailing_active
        pos["current_pct"]     = round(pct_entry, 2)
        pos["peak_volume_m5"]  = peak_vol_m5

        # ── ① Notify when trailing first kicks in ─────────────────────────
        if trailing_active and not was_trailing:
            tg_send(tg_token, tg_chat,
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  🔒 TRAILING STOP LOCKED\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>{sym}</b> ({chain.upper()})  up <b>+{pct_entry:.1f}%</b>\n\n"
                f"    Entry     ${entry:.8f}\n"
                f"    Peak      ${peak:.8f}\n"
                f"    Trail SL  ${trail_stop:.8f}  (-{trail_gap:.0f}% from peak)\n\n"
                f"🔒 Stop rises automatically as price climbs.\n"
                f"📋 <code>{addr}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━"
            )

        # ── ① b) Milestone alerts — celebrate gains, never force exit ─────
        milestones_hit = set(pos.get("milestones_hit") or [])
        for ms in MILESTONE_PCTS:
            if pct_entry >= ms and ms not in milestones_hit:
                milestones_hit.add(ms)
                multiplier = 1 + ms / 100
                emoji = "🚀" if ms >= 200 else ("💰" if ms >= 100 else "🎯")
                tg_send(tg_token, tg_chat,
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"  {emoji} +{ms}% MILESTONE HIT\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"<b>{sym}</b> ({chain.upper()})  <b>+{pct_entry:.1f}%</b>  ({multiplier:.1f}x)\n\n"
                    f"    Entry   ${entry:.8f}\n"
                    f"    Now     ${now_p:.8f}\n"
                    f"    Peak    ${peak:.8f}\n"
                    f"    Trail SL ${trail_stop:.8f}\n\n"
                    f"Still riding — trailing stop protecting gains.\n"
                    f"Consider taking 50% off manually if you want to lock in profit.\n"
                    f"📋 <code>{addr}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
        pos["milestones_hit"] = list(milestones_hit)

        # ── ② Volume decay exit — party is ending, take profit before dump ─
        vol_exit = (
            peak_vol_m5 > 500                              # had real volume at some point
            and vol_m5 < peak_vol_m5 * VOL_DECAY_THRESHOLD  # volume collapsed >85%
            and pct_entry >= VOL_DECAY_MIN_PROFIT          # we're in profit
            and age_min >= VOL_DECAY_MIN_AGE               # not a false signal in first few min
        )
        if vol_exit:
            tg_send(tg_token, tg_chat,
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  📉 VOLUME DECAY EXIT\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>{sym}</b> ({chain.upper()})  <b>+{pct_entry:.1f}%</b>\n\n"
                f"    Entry       ${entry:.8f}\n"
                f"    Exit        ${now_p:.8f}\n"
                f"    Peak vol    ${peak_vol_m5:,.0f}  →  Now ${vol_m5:,.0f}\n\n"
                f"Volume collapsed — exiting before the dump.\n"
                f"📋 <code>{addr}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            _close_pos(pos, now_p, "VOL_DECAY", state)

        # ── ③ Hard TP ─────────────────────────────────────────────────────
        elif pct_entry >= HARD_TP_PCT:
            tg_send(tg_token, tg_chat,
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  🚀 HARD TAKE PROFIT  +{pct_entry:.1f}%\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>{sym}</b> ({chain.upper()})\n\n"
                f"    Entry  ${entry:.8f}\n"
                f"    Now    ${now_p:.8f}  🔥\n"
                f"    Peak   ${peak:.8f}\n\n"
                f"✅ <b>Close your position</b> — massive win!\n"
                f"📋 <code>{addr}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            _close_pos(pos, now_p, "HARD_TP", state)

        # ── ④ Trailing stop hit ───────────────────────────────────────────
        elif trailing_active and now_p <= trail_stop:
            gain_locked = round((trail_stop - entry) / entry * 100, 1)
            tg_send(tg_token, tg_chat,
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  🎯 TRAILING STOP HIT\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>{sym}</b> ({chain.upper()})\n\n"
                f"    Entry    ${entry:.8f}\n"
                f"    Peak     ${peak:.8f}  (+{pct_peak_gain:.1f}%)\n"
                f"    Exit     ${now_p:.8f}\n"
                f"    Locked   <b>+{gain_locked:.1f}%</b> profit ✅\n\n"
                f"🔒 Trailing stop protected your gains.\n"
                f"📋 <code>{addr}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            _close_pos(pos, now_p, "TSL", state)

        # ── ⑤ Fixed SL — only while trailing has never activated ──────────
        elif not trailing_active:
            # ── No-bounce check: straight dump with no meaningful gain ────────
            if (age_min >= eff_no_bounce_age
                    and pct_peak_gain < NO_BOUNCE_PEAK_PCT
                    and pct_entry <= -eff_no_bounce_down):
                tg_send(tg_token, tg_chat,
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"  🛑 NO-BOUNCE EXIT\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"<b>{sym}</b> ({chain.upper()})  <b>{pct_entry:.1f}%</b>\n\n"
                    f"    Entry  ${entry:.8f}\n"
                    f"    Now    ${now_p:.8f}\n"
                    f"    Peak   +{pct_peak_gain:.1f}%  (never bounced)\n\n"
                    f"❌ Straight dump — exit early to protect capital.\n"
                    f"📋 <code>{addr}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
                _close_pos(pos, now_p, "SL", state)

            # ── Standard SL (early window or normal) ─────────────────────────
            else:
                sl_pct = eff_early_sl_pct if age_min <= EARLY_SL_MINUTES else STOP_LOSS_PCT
                conviction_tag = " 🔥 HIGH CONV" if high_conviction else ""
                if pct_entry <= -sl_pct:
                    tg_send(tg_token, tg_chat,
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"  🛑 STOP LOSS HIT\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"<b>{sym}</b> ({chain.upper()})  <b>{pct_entry:.1f}%</b>{conviction_tag}\n\n"
                        f"    Entry  ${entry:.8f}\n"
                        f"    Now    ${now_p:.8f}\n"
                        f"    SL     -{sl_pct:.0f}%{'  (early window)' if age_min <= EARLY_SL_MINUTES else ''}\n\n"
                        f"❌ <b>Exit now</b> — protect your capital.\n"
                        f"📋 <code>{addr}</code>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━"
                    )
                    _close_pos(pos, now_p, "SL", state)
                else:
                    still_open.append(pos)

        else:
            still_open.append(pos)

        time.sleep(0.3)

    state["open"] = still_open
    return state


COOLDOWN_MINUTES = 120   # don't re-enter a token for 2h after exit

def log_paper_trade(state, t):
    """Add a new paper trade when a BUY signal fires."""
    if len(state.get("open", [])) >= MAX_OPEN_TRADES:
        return state

    addr = t.get("address", "")

    # Already tracking this token
    if any(p["address"] == addr for p in state.get("open", [])):
        return state

    # Cooldown — don't re-enter a token we recently closed
    cooldowns = state.get("cooldown", {})
    if addr in cooldowns:
        exited_at = datetime.datetime.fromisoformat(cooldowns[addr])
        if exited_at.tzinfo is None:
            exited_at = exited_at.replace(tzinfo=datetime.UTC)
        age_min   = (datetime.datetime.now(datetime.UTC) - exited_at).total_seconds() / 60
        if age_min < COOLDOWN_MINUTES:
            print(f"  Cooldown: {t.get('symbol')} skipped ({age_min:.0f}m since last exit)")
            return state

    price = t.get("price_usd")
    state.setdefault("open", []).append({
        "symbol":          t.get("symbol", "?"),
        "chain":           t.get("chain", "?"),
        "address":         addr,
        "source":          t.get("source", ""),
        "entry_price":     float(price) if price else None,
        "peak_price":      float(price) if price else None,
        "trailing_active": False,
        "entry_time":      datetime.datetime.now(datetime.UTC).isoformat(),
        "score":           t.get("score", 0),
        "status":          "open",
    })
    sheets_log_open(t)
    return state




# ── Pump.fun ──────────────────────────────────────────────────────────────────

def check_smart_money_pump(mint):
    trades = curl(f"https://frontend-api.pump.fun/trades/all/{mint}?limit=50&offset=0") or []
    found  = set()
    for trade in trades:
        if not trade.get("is_buy", True):
            continue
        wallet = trade.get("user", "")
        if wallet in SMART_WALLETS:
            found.add(SMART_WALLETS[wallet])
    return list(found)


def fetch_pump_tokens():
    coins = curl(
        "https://frontend-api.pump.fun/coins"
        "?sort=last_trade_timestamp&order=DESC&limit=50&includeNsfw=false"
    ) or []
    tokens  = []
    now_ts  = time.time()
    for coin in coins:
        mint = coin.get("mint", "")
        if not mint or coin.get("complete"):
            continue
        progress = coin.get("bonding_curve_progress") or coin.get("progress") or 0
        mcap     = coin.get("usd_market_cap") or coin.get("market_cap") or 0
        created  = coin.get("created_timestamp") or 0
        age_min  = (now_ts - created / 1000) / 60 if created else 9999
        trades_n = coin.get("total_trade_count") or 0
        if (mcap < PUMP_MIN_MCAP or progress < PUMP_MIN_PROGRESS
                or progress > PUMP_MAX_PROGRESS or age_min > PUMP_MAX_AGE_MINUTES
                or trades_n < PUMP_MIN_TRADES):
            continue
        smart = check_smart_money_pump(mint)
        time.sleep(0.1)
        tokens.append({
            "chain": "solana", "address": mint,
            "symbol": coin.get("symbol", "?"), "name": coin.get("name", "?"),
            "source": "pump.fun", "description": coin.get("description", ""),
            "icon":   coin.get("image_uri", ""),
            "pair_age_minutes": round(age_min, 1),
            "liquidity_usd": mcap * 0.08,
            "volume_h1": 0, "volume_h24": 0,
            "price_change_h1": None, "price_change_h24": None,
            "buys_h1": trades_n, "sells_h1": 0,
            "price_usd": None, "fdv": mcap,
            "pair_url": f"https://pump.fun/{mint}",
            "dex_id": "pump.fun",
            "pump_progress": round(progress, 1),
            "smart_money": smart,
        })
    return tokens


# ── Graduated pump.fun tokens (bonding curve complete → Raydium) ─────────────

GRAD_MAX_AGE_MINUTES = 240   # extended — organic pumps can take time to be discovered
GRAD_MIN_MCAP        = 10_000  # lowered from $50K — don't miss early grads like SCAM ALTMAN

def fetch_graduated_tokens():
    """
    Query pump.fun v3 API for tokens that completed the bonding curve and moved
    to Raydium. These are invisible to DexScreener boost/profile feeds — this is
    why we missed SCAM ALTMAN, CLAWD, etc.

    Filter: complete=True, raydium_pool set, age < GRAD_MAX_AGE_MINUTES, mcap > GRAD_MIN_MCAP.
    Returns seed dicts with source='graduated' for enrichment via enrich().
    """
    data = curl(
        "https://frontend-api-v3.pump.fun/coins"
        "?sort=last_trade_timestamp&order=DESC&limit=100&includeNsfw=false"
    )
    if not data or not isinstance(data, list):
        return []

    tokens  = []
    now_ts  = time.time()

    for coin in data:
        # Must be fully graduated with a live Raydium pool
        if not coin.get("complete"):
            continue
        pool = coin.get("raydium_pool") or coin.get("raydium_pool_address") or ""
        if not pool:
            continue

        mint    = coin.get("mint", "")
        if not mint:
            continue

        mcap    = coin.get("usd_market_cap") or coin.get("market_cap") or 0
        created = coin.get("created_timestamp") or 0
        age_min = (now_ts - created / 1000) / 60 if created else 9999

        if mcap < GRAD_MIN_MCAP or age_min > GRAD_MAX_AGE_MINUTES:
            continue

        tokens.append({
            "chain":           "solana",
            "address":         mint,
            "symbol":          coin.get("symbol", "?"),
            "name":            coin.get("name", "?"),
            "source":          "graduated",
            "description":     coin.get("description", ""),
            "icon":            coin.get("image_uri", ""),
            "pair_age_minutes": round(age_min, 1),
            "raydium_pool":    pool,
        })

    print(f"  Graduated candidates: {len(tokens)}")
    return tokens


# ── DexScreener new pairs — catches tokens before boost/profile feeds ─────────
# DexScreener indexes new pools within 1-3 min of creation. This hits the
# chain-level pair list sorted by creation time, giving us tokens 5-10 min
# before they appear in the boost or profile feeds the main scan uses.

NEW_PAIRS_CHAINS    = ["solana", "bsc", "base"]   # highest meme activity
NEW_PAIRS_MAX_AGE   = 45                           # only pairs < 45 min old
NEW_PAIRS_MIN_LIQ   = 5_000                        # $5K min — filters empty pools

def fetch_new_dex_pairs():
    tokens = {}
    for chain in NEW_PAIRS_CHAINS:
        data = curl(f"https://api.dexscreener.com/latest/dex/pairs/{chain}") or {}
        pairs = data.get("pairs") or []
        now_ms = time.time() * 1000
        for p in pairs:
            created = p.get("pairCreatedAt") or 0
            if not created:
                continue
            age_min = (now_ms - created) / 60000
            if age_min > NEW_PAIRS_MAX_AGE:
                continue
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if liq < NEW_PAIRS_MIN_LIQ:
                continue
            addr = (p.get("baseToken") or {}).get("address", "")
            if not addr or addr in tokens:
                continue
            tokens[addr] = {
                "chain":   chain,
                "address": addr,
                "symbol":  (p.get("baseToken") or {}).get("symbol", "?"),
                "name":    (p.get("baseToken") or {}).get("name", "?"),
                "source":  "new_pair",
                "icon":    (p.get("info") or {}).get("imageUrl", ""),
            }
        time.sleep(0.1)
    result = list(tokens.values())
    print(f"  New DEX pairs (<{NEW_PAIRS_MAX_AGE}min): {len(result)}")
    return result


# ── DexScreener fetch ─────────────────────────────────────────────────────────

def fetch_tokens():
    """
    Pull candidates from DexScreener boosted tokens + new token profiles.
    OKX X Layer removed — DexScreener already covers those chains.
    """
    tokens = {}

    # ① Boosted (paid promotions — teams that push here tend to have liquidity)
    boosted = curl("https://api.dexscreener.com/token-boosts/top/v1") or []
    for t in boosted:
        k = (t.get("chainId", ""), t.get("tokenAddress", ""))
        if k[0] and k[1]:
            tokens[k] = {"chain": k[0], "address": k[1], "source": "boost",
                         "description": t.get("description", ""),
                         "icon": t.get("icon", "") or t.get("header", "")}

    # ② Freshest token profiles (new listings)
    profiles = curl("https://api.dexscreener.com/token-profiles/latest/v1") or []
    for t in profiles:
        k = (t.get("chainId", ""), t.get("tokenAddress", ""))
        if k[0] and k[1] and k not in tokens:
            tokens[k] = {"chain": k[0], "address": k[1], "source": "new",
                         "description": t.get("description", ""),
                         "icon": t.get("icon", "") or t.get("header", "")}

    return list(tokens.values())


def enrich(token):
    chain = token["chain"]
    addr  = token["address"]

    data  = curl(f"https://api.dexscreener.com/latest/dex/tokens/{addr}") or {}
    pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == chain]
    if not pairs:
        return None
    p      = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
    age_ms = p.get("pairCreatedAt")
    age_min = round((time.time() - age_ms / 1000) / 60, 1) if age_ms else None

    # Prefer header (chart image) over logo for the photo alert
    info   = p.get("info") or {}
    icon   = token.get("icon", "") or info.get("header", "") or info.get("imageUrl", "")

    return {
        **token,
        "icon": icon,
        "symbol":           p.get("baseToken", {}).get("symbol", "?"),
        "name":             p.get("baseToken", {}).get("name", "?"),
        "price_usd":        p.get("priceUsd"),
        "liquidity_usd":    (p.get("liquidity") or {}).get("usd") or 0,
        "volume_h1":        (p.get("volume") or {}).get("h1"),
        "volume_h24":       (p.get("volume") or {}).get("h24"),
        "price_change_m5":  (p.get("priceChange") or {}).get("m5"),
        "price_change_h1":  (p.get("priceChange") or {}).get("h1"),
        "price_change_h6":  (p.get("priceChange") or {}).get("h6"),
        "price_change_h24": (p.get("priceChange") or {}).get("h24"),
        "buys_h1":          (p.get("txns") or {}).get("h1", {}).get("buys"),
        "sells_h1":         (p.get("txns") or {}).get("h1", {}).get("sells"),
        "fdv":              p.get("fdv"),
        "pair_age_minutes": age_min,
        "pair_url":         p.get("url"),
        "dex_id":           p.get("dexId"),
        "smart_money":      [],
        "pump_progress":    None,
    }


# ── Score ──────────────────────────────────────────────────────────────────────

def score(t):
    s     = 0
    liq   = t.get("liquidity_usd") or 0
    vol24 = t.get("volume_h24") or 1
    pc1   = t.get("price_change_h1") or 0
    buys  = t.get("buys_h1") or 0
    sells = t.get("sells_h1") or 1
    age   = t.get("pair_age_minutes")
    src   = t.get("source", "")
    smart = t.get("smart_money", [])
    prog  = t.get("pump_progress")

    if pc1 > 50:   s += 25
    elif pc1 > 20: s += 18
    elif pc1 > 5:  s += 10
    elif pc1 > 0:  s += 5

    vlr = vol24 / liq if liq > 0 else 0
    if vlr > 30:   s += 25
    elif vlr > 10: s += 18
    elif vlr > 3:  s += 10
    elif vlr > 1:  s += 5

    bsr = buys / sells if sells > 0 else 0
    if bsr > 3:     s += 20
    elif bsr > 2:   s += 15
    elif bsr > 1.5: s += 10
    elif bsr > 1:   s += 5

    if liq > 200000: s += 15
    elif liq > 50000:s += 10
    elif liq > 20000:s += 6
    elif liq > 5000: s += 3

    if src == "boost":       s += 8
    if src == "pump.fun":    s += 10
    if src == "graduated":   s += 12   # completed bonding curve → organic community token
    if src == "whale_buy":   s += 18   # direct whale buy — high confidence
    if src == "x_alpha":     s += 14   # trusted alpha caller call on X
    if src == "tg_alpha":    s += 12 + min(t.get("tg_mentions", 1) - 1, 6)  # +1 per extra mention, max +6
    if src == "gmgn":        s += 10   # organically trending on Solana — no paid promotion
    if src == "birdeye":     s += 11   # new listing or trending on Birdeye real-time feed
    if src == "new_pair":    s += 9    # brand-new pool (<45 min) — earliest possible entry
    if smart:                s += 12
    # Extra boost if whale win-rate is very high
    wr = t.get("whale_win_rate", 0)
    if wr >= 0.85:           s += 5
    if age is not None and age < 60:    s += 7
    elif age is not None and age < 180: s += 3
    if prog is not None and 20 <= prog <= 70: s += 5
    if t.get("parabolic"):   s += 15   # parabolic bypass tokens get conviction bonus
    src_count = t.get("source_count", 1)
    if src_count >= 3:       s += 20   # 3+ independent feeds all spotted this token
    elif src_count == 2:     s += 12   # confirmed by 2 feeds

    if s >= STRONG_BUY_SCORE:    verdict = "STRONG_BUY"
    elif s >= 65:                verdict = "BUY"
    elif s >= 45:                verdict = "WATCH"
    else:                        verdict = "AVOID"
    return {**t, "score": s, "verdict": verdict}


# ── Rug check ─────────────────────────────────────────────────────────────────

CHAIN_IDS = {"ethereum": "1", "bsc": "56", "base": "8453", "arbitrum": "42161"}

def rug_check(t):
    chain = t.get("chain", "")
    addr  = t.get("address", "")
    src   = t.get("source", "")
    if chain == "solana" or src == "pump.fun":
        d = curl(f"https://api.rugcheck.xyz/v1/tokens/{addr}/report/summary") or {}
        sv = d.get("score", 0)
        risks = [r.get("name", "") for r in d.get("risks", [])]
        bad = any(r in ["Freeze Authority still enabled", "Mint Authority still enabled"]
                  for r in risks)
        return {"safe": not bad and sv < 500, "detail": f"score {sv}", "risks": risks[:3]}
    elif chain in CHAIN_IDS:
        d  = curl(f"https://api.honeypot.is/v2/IsHoneypot?address={addr}&chainID={CHAIN_IDS[chain]}") or {}
        hp = (d.get("honeypotResult") or {}).get("isHoneypot", False)
        st = (d.get("simulationResult") or {}).get("sellTax", 0) or 0
        return {"safe": not hp and st <= 10, "detail": f"sell tax {st:.0f}%", "honeypot": hp}
    return {"safe": True, "detail": "unchecked"}


# ── Buy links ─────────────────────────────────────────────────────────────────

GMGN_CHAIN = {"solana": "sol", "ethereum": "eth", "bsc": "bsc",
              "base": "base", "arbitrum": "arb"}

def buy_links(chain, addr, pair_url):
    links = []
    if chain in GMGN_CHAIN:
        links.append(f"<a href='https://gmgn.ai/{GMGN_CHAIN[chain]}/token/{addr}'>GMGN</a>")
    if chain == "solana":
        links.append(f"<a href='https://photon-sol.tinyastro.io/en/lp/{addr}'>Photon</a>")
        links.append(f"<a href='https://bullx.io/terminal?chainId=1399811149&address={addr}'>BullX</a>")
        links.append(f"<a href='https://t.me/TrojanSwapBot?start={addr}'>Trojan</a>")
    elif chain in ("ethereum", "bsc", "base", "arbitrum"):
        links.append(f"<a href='https://dextools.io/app/en/{chain}/pair-explorer/{addr}'>DEXTools</a>")
        links.append(f"<a href='https://app.uniswap.org/explore/tokens/{chain}/{addr}'>Uniswap</a>")
    if pair_url:
        links.append(f"<a href='{pair_url}'>📊 Chart</a>")
    return " · ".join(links)


# ── Score bar helper ──────────────────────────────────────────────────────────

def score_bar(sc):
    filled = round(sc / 10)
    empty  = 10 - filled
    return "█" * filled + "░" * empty


def momentum_arrow(pct):
    if pct is None:    return "➖ N/A"
    if pct >= 50:      return f"🚀 +{pct:.1f}%"
    if pct >= 20:      return f"📈 +{pct:.1f}%"
    if pct >= 5:       return f"↗️ +{pct:.1f}%"
    if pct >= 0:       return f"➡️ +{pct:.1f}%"
    if pct >= -10:     return f"↘️ {pct:.1f}%"
    return f"📉 {pct:.1f}%"


# ── Format alert ──────────────────────────────────────────────────────────────

def format_alert(t, rc):
    verdict = t.get("verdict", "?")
    sym     = t.get("symbol", "?")
    name    = t.get("name", "")
    chain   = t.get("chain", "?")
    addr    = t.get("address", "")
    sc      = t.get("score", 0)
    price   = t.get("price_usd")
    liq     = t.get("liquidity_usd") or 0
    vol1    = t.get("volume_h1") or 0
    vol24   = t.get("volume_h24") or 0
    pc5     = t.get("price_change_m5")
    pc1     = t.get("price_change_h1")
    pc6     = t.get("price_change_h6")
    pc24    = t.get("price_change_h24")
    buys    = t.get("buys_h1") or 0
    sells   = t.get("sells_h1") or 0
    bsr     = round(buys / sells, 2) if sells else buys
    age     = t.get("pair_age_minutes")
    url     = t.get("pair_url", "")
    src     = t.get("source", "")
    smart   = t.get("smart_money", [])
    prog      = t.get("pump_progress")
    fdv       = t.get("fdv")
    reasoning = t.get("reasoning", "")
    entry_note= t.get("entry_note", "")
    ai_scored = t.get("ai_scored", False)
    all_srcs  = t.get("all_sources", [])

    # ── Labels
    verdict_badge = {
        "STRONG_BUY": "🔥 STRONG BUY",
        "BUY":        "🟢 BUY",
        "WATCH":      "🟡 WATCH",
        "AVOID":      "🔴 AVOID",
    }.get(verdict, "⚪")
    chain_upper   = chain.upper()
    age_str       = f"{int(age)}m" if age is not None else "?"
    price_str     = f"${float(price):.8f}" if price else "—"
    fdv_str       = f"${fdv/1e6:.2f}M" if fdv and fdv >= 1e6 else (f"${fdv:,.0f}" if fdv else "—")
    liq_str       = f"${liq/1e3:.1f}K" if liq < 1e6 else f"${liq/1e6:.2f}M"
    vol1_str      = f"${vol1/1e3:.1f}K" if vol1 < 1e6 else f"${vol1/1e6:.2f}M"
    vol24_str     = f"${vol24/1e3:.1f}K" if vol24 < 1e6 else f"${vol24/1e6:.2f}M"
    vlr           = round(vol24 / liq, 1) if liq > 0 else 0
    rug_icon      = "✅" if rc.get("safe") else ("🚨" if rc.get("honeypot") else "⚠️")
    rug_label     = "Clean" if rc.get("safe") else ("HONEYPOT" if rc.get("honeypot") else rc.get("detail","?"))
    src_badge     = "🔥 PUMP.FUN" if src == "pump.fun" else ("⚡ Boosted" if src == "boost" else "🆕 New Profile")
    if t.get("parabolic"):
        src_badge = "🚀 PARABOLIC"
    bar           = score_bar(sc)

    lines = [
        f"━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"  {verdict_badge}  ·  <b>{sym}</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"",
        f"🏷  <b>{sym}</b>",
    ]
    if name and name != sym:
        lines.append(f"    <i>{name}</i>")
    lines += [
        f"⛓  {chain_upper}  ·  {src_badge}",
        f"⏱  Age: <b>{age_str}</b>  ·  Score: <b>{sc}/100</b>{'  🤖 AI' if ai_scored else ''}",
        f"    [{bar}]",
        f"",
        f"─── 💰 PRICE ───────────────",
        f"    {price_str}",
        f"    FDV {fdv_str}  ·  Liq {liq_str}",
        f"",
        f"─── 📈 MOMENTUM ────────────",
        f"    5m   {momentum_arrow(pc5)}",
        f"    1h   {momentum_arrow(pc1)}",
        f"    6h   {momentum_arrow(pc6)}",
        f"    24h  {momentum_arrow(pc24)}",
        f"",
        f"─── 📊 VOLUME & FLOW ───────",
        f"    1h  {vol1_str}  ·  24h {vol24_str}",
        f"    Vol/Liq ratio: <b>{vlr}×</b>",
        f"    Buys: <b>{buys}</b>  ·  Sells: <b>{sells}</b>  ·  BSR <b>{bsr}</b>",
    ]

    if prog is not None:
        prog_bar = "█" * int(prog / 10) + "░" * (10 - int(prog / 10))
        lines += [
            f"",
            f"─── 🎯 BONDING CURVE ───────",
            f"    [{prog_bar}] {prog:.0f}%  to graduation",
        ]

    x_caller = t.get("x_caller", "")
    if x_caller:
        x_likes = t.get("x_likes", 0)
        x_rts   = t.get("x_rts", 0)
        lines += [
            f"",
            f"─── 🐦 X ALPHA CALL ────────",
            f"    {x_caller}  ❤ {x_likes}  🔁 {x_rts}",
        ]
        snippet = t.get("x_snippet", "")
        if snippet:
            lines.append(f"    <i>{snippet[:80]}…</i>")

    if smart or t.get("whale_label"):
        lines += [
            f"",
            f"─── 🐋 WHALE SIGNAL ────────",
        ]
        whale_label = t.get("whale_label")
        whale_wr    = t.get("whale_win_rate")
        whale_exit  = t.get("whale_exit_pct")
        whale_tp    = t.get("whale_exit_tp")
        if whale_label:
            wr_str = f"  WR {whale_wr*100:.0f}%" if whale_wr else ""
            lines.append(f"    🎯 <b>{whale_label}</b>{wr_str}")
            if whale_exit:
                lines.append(f"    Whale exits ~+{whale_exit}%  →  We exit ~+{whale_tp}%")
        for w in smart:
            if w != whale_label:
                lines.append(f"    ✦ {w}")

    lines += [
        f"",
        f"─── 🛡 SAFETY ──────────────",
        f"    {rug_icon} {rug_label}  ·  {rc.get('detail','')}",
    ]

    if PAPER_MODE:
        lines += [
            f"",
            f"─── 📝 PAPER TRADE ─────────",
            f"    Entry  {price_str}",
            f"    🛑 SL -{STOP_LOSS_PCT:.0f}%  →  🔒 Trail +{TRAIL_ACTIVATE_PCT:.0f}% (-{TRAIL_PCT:.0f}% peak)  →  🎯 {MILESTONE_PCTS[0]}% / {MILESTONE_PCTS[1]}% / {MILESTONE_PCTS[2]}%  →  🚀 TP +{HARD_TP_PCT:.0f}%",
        ]

    if len(all_srcs) > 1:
        lines += [
            f"",
            f"─── 🔀 MULTI-SOURCE ({len(all_srcs)}) ───────",
            f"    " + "  ·  ".join(all_srcs),
        ]

    if ai_scored and reasoning:
        lines += [
            f"",
            f"─── 🤖 AI ANALYSIS ─────────",
            f"    <i>{reasoning[:140]}</i>",
        ]
        if entry_note:
            lines.append(f"    📌 {entry_note[:100]}")

    lines += [
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📋 <code>{addr}</code>",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    links = buy_links(chain, addr, url)
    if links:
        lines.append(f"🛒 {links}")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def _source_win_rates(state):
    """Compute win rate per source from closed paper trades (min 3 trades to count)."""
    from collections import defaultdict
    wins = defaultdict(int)
    total = defaultdict(int)
    avg_pct = defaultdict(list)
    for t in state.get("closed", []):
        src = t.get("source", "unknown")
        total[src] += 1
        pct = t.get("exit_pct") or 0
        avg_pct[src].append(pct)
        if t.get("status") in ("TP", "TSL", "HARD_TP", "VOL_DECAY", "WHALE_EXIT"):
            wins[src] += 1
    return {
        src: {
            "win_rate": round(wins[src] / total[src] * 100),
            "trades":   total[src],
            "avg_pct":  round(sum(avg_pct[src]) / len(avg_pct[src]), 1),
        }
        for src in total if total[src] >= 3
    }


def main():
    tg_token   = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat    = os.environ["TELEGRAM_CHAT_ID"]
    helius_key = os.environ.get("HELIUS_API_KEY", "")   # optional — enables whale scanning

    now    = datetime.datetime.utcnow().strftime("%H:%M UTC")
    hour   = datetime.datetime.utcnow().hour
    minute = datetime.datetime.utcnow().minute
    print(f"[{now}] Signal Scout v5 scan starting... (mode={TRADE_MODE})")

    # ── Load state (commands handled exclusively by commands.py every 1 min) ───
    state = load_state()

    # ── Self-learning: print source win rates from history ────────────────────
    wr = _source_win_rates(state)
    if wr:
        best = sorted(wr.items(), key=lambda x: x[1]["win_rate"], reverse=True)
        parts = [f"{s}: {d['win_rate']}% WR ({d['trades']}T, avg {d['avg_pct']:+.0f}%)" for s, d in best]
        print(f"  📊 Source performance: {' | '.join(parts)}")

    if state.get("paused"):
        print("  Bot is paused — skipping scan.")
        save_state(state)
        return

    # ── Check paper positions for TP / SL ─────────────────────────────────────
    if state.get("open"):
        print(f"  Checking {len(state['open'])} paper positions...")
        state = check_exits(state, tg_token, tg_chat)

    # ── Check real positions for TP / SL ──────────────────────────────────────
    if TRADE_MODE != "paper":
        check_real_exits(tg_token, tg_chat)

    # ── Whale buy scanner (Helius) ─────────────────────────────────────────────
    whale_signals = []
    if helius_key:
        print("  Scanning whale wallets...")
        whale_signals = get_whale_buys(helius_key, lookback_minutes=15)
        print(f"  Whale signals: {len(whale_signals)}")

    # ── Whale exit check for open positions ───────────────────────────────────
    if helius_key and state.get("open"):
        open_mints = {p["address"] for p in state["open"] if p.get("chain") == "solana"}
        if open_mints:
            selling = get_whale_exits(helius_key, open_mints, lookback_minutes=10)
            still_open_whale = []
            for pos in state.get("open", []):
                if pos["address"] in selling:
                    print(f"  Whale exiting {pos['symbol']} — force closing position")
                    addr  = pos["address"]
                    chain = pos["chain"]
                    entry = pos.get("entry_price") or 0
                    # get current price for clean exit record
                    _d    = curl(f"https://api.dexscreener.com/latest/dex/tokens/{addr}") or {}
                    _pairs = [_p for _p in (_d.get("pairs") or []) if _p.get("chainId") == chain]
                    if _pairs:
                        _best = max(_pairs, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
                        now_p = float(_best.get("priceUsd") or 0)
                    else:
                        now_p = entry * (1 + (pos.get("current_pct") or 0) / 100)
                    pct_now = round((now_p - entry) / entry * 100, 2) if entry else 0
                    tg_send(tg_token, tg_chat,
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"  🐋 WHALE EXIT — CLOSING\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"Tracked whale <b>SOLD {pos['symbol']}</b> — auto-closing position.\n\n"
                        f"    Entry  ${entry:.8f}\n"
                        f"    Exit   ${now_p:.8f}  (<b>{pct_now:+.1f}%</b>)\n\n"
                        f"Front-ran the whale exit.\n"
                        f"📋 <code>{addr}</code>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━"
                    )
                    _close_pos(pos, now_p, "WHALE_EXIT", state)
                else:
                    still_open_whale.append(pos)
            state["open"] = still_open_whale

    # ── Fetch DEX candidates ───────────────────────────────────────────────────
    raw = fetch_tokens()
    print(f"  DEX candidates: {len(raw)}")

    pump_tokens = fetch_pump_tokens()
    print(f"  Pump.fun candidates: {len(pump_tokens)}")

    enriched = []
    for t in raw:
        try:
            e = enrich(t)
            if e:
                enriched.append(e)
            time.sleep(0.05)
        except Exception as ex:
            print(f"  enrich error {t.get('address','?')[:10]}: {ex}")

    enriched.extend(pump_tokens)

    # ── Enrich whale signals (fetch DexScreener data if available) ────────────
    enriched_whale = []
    for w in whale_signals:
        try:
            e = enrich(w)
            if e:
                # Preserve whale-specific fields
                e["whale_label"]    = w.get("whale_label")
                e["whale_win_rate"] = w.get("whale_win_rate")
                e["whale_exit_pct"] = w.get("whale_exit_pct")
                e["whale_exit_tp"]  = w.get("whale_exit_tp")
                e["source"]         = "whale_buy"
                e["smart_money"]    = w.get("smart_money", [])
                enriched_whale.append(e)
            else:
                enriched_whale.append(w)   # use raw data if DexScreener has nothing yet
            time.sleep(0.05)
        except Exception as ex:
            enriched_whale.append(w)       # keep whale signal even without DEX data

    enriched.extend(enriched_whale)

    # ── X/Twitter alpha signals ────────────────────────────────────────────────
    x_raw = fetch_x_tokens()
    enriched_x = []
    for x in x_raw:
        try:
            e = enrich(x)
            if e:
                e["source"]    = "x_alpha"
                e["x_caller"]  = x.get("x_caller", "")
                e["x_likes"]   = x.get("x_likes", 0)
                e["x_rts"]     = x.get("x_rts", 0)
                e["x_snippet"] = x.get("x_snippet", "")
                enriched_x.append(e)
            time.sleep(0.05)
        except Exception as ex:
            print(f"  x enrich error {x.get('address','?')[:10]}: {ex}")
    enriched.extend(enriched_x)

    # ── Telegram alpha channel signals ────────────────────────────────────────
    tg_raw = fetch_tg_alpha_tokens()
    enriched_tg = []
    for tg in tg_raw:
        try:
            e = enrich(tg)
            if e:
                e["source"]       = "tg_alpha"
                e["tg_channels"]  = tg.get("tg_channels", "")
                e["tg_mentions"]  = tg.get("tg_mentions", 1)
                e["tg_snippet"]   = tg.get("tg_snippet", "")
                enriched_tg.append(e)
            time.sleep(0.05)
        except Exception as ex:
            print(f"  tg enrich error {tg.get('address','?')[:10]}: {ex}")
    enriched.extend(enriched_tg)

    # ── Birdeye new listings + trending ───────────────────────────────────────
    birdeye_raw = fetch_birdeye_tokens()
    enriched_birdeye = []
    for be in birdeye_raw:
        try:
            e = enrich(be)
            if e:
                e["source"]          = "birdeye"
                e["birdeye_type"]    = be.get("birdeye_type", "")
                e["birdeye_volume"]  = be.get("birdeye_volume", 0)
                enriched_birdeye.append(e)
            time.sleep(0.05)
        except Exception as ex:
            print(f"  birdeye enrich error {be.get('address','?')[:10]}: {ex}")
    enriched.extend(enriched_birdeye)

    # ── GMGN trending Solana tokens ────────────────────────────────────────────
    gmgn_raw = fetch_gmgn_tokens()
    enriched_gmgn = []
    for gm in gmgn_raw:
        try:
            e = enrich(gm)
            if e:
                e["source"]         = "gmgn"
                e["gmgn_swaps_1h"]  = gm.get("gmgn_swaps_1h", 0)
                e["gmgn_volume_1h"] = gm.get("gmgn_volume_1h", 0)
                enriched_gmgn.append(e)
            time.sleep(0.05)
        except Exception as ex:
            print(f"  gmgn enrich error {gm.get('address','?')[:10]}: {ex}")
    enriched.extend(enriched_gmgn)

    # ── Graduated pump.fun tokens (bonding curve complete → Raydium) ──────────
    grad_raw = fetch_graduated_tokens()
    enriched_grad = []
    for g in grad_raw:
        try:
            e = enrich(g)
            if e:
                e["source"]      = "graduated"
                e["icon"]        = e.get("icon") or g.get("icon", "")
                enriched_grad.append(e)
            time.sleep(0.05)
        except Exception as ex:
            print(f"  grad enrich error {g.get('address','?')[:10]}: {ex}")
    enriched.extend(enriched_grad)

    # ── New DEX pairs — freshest possible entries ──────────────────────────────
    new_pairs_raw = fetch_new_dex_pairs()
    enriched_new_pairs = []
    for np in new_pairs_raw:
        try:
            e = enrich(np)
            if e:
                e["source"] = "new_pair"
                enriched_new_pairs.append(e)
            time.sleep(0.05)
        except Exception as ex:
            print(f"  new_pair enrich error {np.get('address','?')[:10]}: {ex}")
    enriched.extend(enriched_new_pairs)

    print(f"  Total enriched: {len(enriched)}  (🐋 {len(enriched_whale)} whale  🐦 {len(enriched_x)} X  💬 {len(enriched_tg)} TG  📈 {len(enriched_gmgn)} GMGN  🦅 {len(enriched_birdeye)} Birdeye  🎓 {len(enriched_grad)} graduated  🆕 {len(enriched_new_pairs)} new_pairs)")

    # ── Multi-source merge — same token in multiple feeds = high conviction ────
    _src_priority = {"whale_buy":7,"tg_alpha":6,"x_alpha":5,"graduated":4,
                     "gmgn":3,"birdeye":2,"new_pair":2,"boost":1,"pump.fun":1,"new":0}
    _addr_map = {}
    for t in enriched:
        addr = t.get("address", "")
        if not addr:
            continue
        if addr not in _addr_map:
            _addr_map[addr] = []
        _addr_map[addr].append(t)
    enriched = []
    for addr, tokens in _addr_map.items():
        best = max(tokens, key=lambda t: _src_priority.get(t.get("source", ""), 0))
        all_srcs = list({t.get("source", "") for t in tokens if t.get("source")})
        enriched.append({**best, "source_count": len(tokens), "all_sources": all_srcs})
    multi_count = sum(1 for t in enriched if t.get("source_count", 1) > 1)
    if multi_count:
        print(f"  Multi-source confirmed: {multi_count} tokens seen in 2+ feeds 🔥")

    # ── Filter ─────────────────────────────────────────────────────────────────
    fresh = []
    seen_addrs = set()
    for t in enriched:
        addr  = t.get("address", "")
        age   = t.get("pair_age_minutes") or 9999
        liq   = t.get("liquidity_usd") or 0
        vol24 = t.get("volume_h24") or 0
        pc1   = t.get("price_change_h1") or 0      # signed — no abs(), declining tokens must not pass
        m5    = t.get("price_change_m5") or 0       # 5m momentum check
        src   = t.get("source", "")
        smart = t.get("smart_money", [])
        if addr in seen_addrs:
            continue
        seen_addrs.add(addr)
        fdv = t.get("fdv") or 0
        if src == "whale_buy":
            fresh.append(t)   # always trust whale buys
        elif src == "pump.fun":
            if age <= PUMP_MAX_AGE_MINUTES and (t.get("buys_h1") or 0) >= PUMP_MIN_TRADES:
                fresh.append(t)
        elif src == "graduated":
            # Recently graduated pump.fun tokens — use relaxed age, skip FDV cap
            if liq >= MIN_LIQUIDITY and (pc1 >= MIN_MOMENTUM_H1 or bool(smart)):
                fresh.append(t)
        elif (pc1 >= 100
              and age <= 360
              and liq >= MIN_LIQUIDITY
              and vol24 >= MIN_VOLUME_24H
              and m5 >= MIN_MOMENTUM_M5):
            # Parabolic bypass — skip FDV cap when token is flying +100% in 1h.
            # FDV cap of $5M kills tokens like UHOOT that blast through it in the
            # first few percent of a 95k% move. Still requires real liquidity/volume.
            t["parabolic"] = True
            fresh.append(t)
        elif (age <= MAX_AGE_MINUTES
              and liq >= MIN_LIQUIDITY
              and vol24 >= MIN_VOLUME_24H
              and (fdv == 0 or fdv <= MAX_FDV)
              and (pc1 >= MIN_MOMENTUM_H1 or bool(smart))
              and (m5 >= MIN_MOMENTUM_M5 or bool(smart))):   # 5m must not be actively dumping
            fresh.append(t)

    print(f"  Fresh signals: {len(fresh)}")

    # ── Score ──────────────────────────────────────────────────────────────────
    scored = [score(t) for t in fresh]
    scored.sort(key=lambda x: x["score"], reverse=True)

    # ── Claude AI re-score — runs on top candidates when API key is set ────────
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from scorer import score_token as _ai_score
            top_n = min(10, len(scored))
            print(f"  Claude AI scoring top {top_n} candidates...")
            for i in range(top_n):
                t = scored[i]
                if t.get("score", 0) < 40:
                    break
                ai = _ai_score(t)
                if ai and ai.get("score_total"):
                    # 50/50 blend: rule-based anchors to data, AI adds nuance
                    blended = round(0.5 * t["score"] + 0.5 * ai["score_total"])
                    scored[i] = {
                        **t,
                        "score":      blended,
                        "verdict":    ai.get("verdict", t["verdict"]),
                        "reasoning":  ai.get("reasoning", ""),
                        "entry_note": ai.get("entry_note", ""),
                        "ai_scored":  True,
                    }
            scored.sort(key=lambda x: x["score"], reverse=True)
        except Exception as _ae:
            print(f"  Claude scoring skipped: {_ae}")

    # ── Alert + log ────────────────────────────────────────────────────────────
    alerts_sent = 0
    for t in scored[:MAX_TOKENS]:
        has_smart = bool(t.get("smart_money")) or bool(t.get("whale_label")) or t.get("source") in ("x_alpha", "tg_alpha") or t.get("parabolic")
        is_whale  = t.get("source") == "whale_buy"
        # Non-whale path: need score >= MIN_SCORE and not AVOID/WATCH
        if not has_smart:
            if t["score"] < MIN_SCORE:
                continue
            if t["verdict"] not in ("BUY", "STRONG_BUY"):
                continue
        else:
            # Whale/smart override: still needs a quality floor
            if t["score"] < WHALE_MIN_SCORE:
                continue
            if t["verdict"] == "AVOID":
                continue

        rc = rug_check(t)
        if not rc.get("safe"):
            print(f"  Skipped (rug): {t.get('symbol')} — {rc}")
            continue

        msg  = format_alert(t, rc)
        icon = t.get("icon", "")
        ok   = tg_send_photo(tg_token, tg_chat, icon, msg) if (icon and icon.startswith("http")) \
               else tg_send(tg_token, tg_chat, msg)

        if ok:
            alerts_sent += 1
            flags  = " 🐋" if is_whale else (" 💡" if has_smart else "")
            flags += " 🔥" if t.get("source") == "pump.fun" else ""
            print(f"  Alert: {t.get('symbol')} score={t['score']} age={t.get('pair_age_minutes')}m{flags}")
            post_tweet(t)
            if PAPER_MODE:
                state = log_paper_trade(state, t)
            # Auto/semi trading
            maybe_trade(t, tg_token, tg_chat)
        time.sleep(1)

    print(f"  Done. {alerts_sent} alerts from {len(scored)} scored tokens.")

    # ── Status ping ────────────────────────────────────────────────────────────
    if alerts_sent == 0:
        pump_count  = sum(1 for t in enriched if t.get("source") == "pump.fun")
        whale_count = sum(1 for t in enriched if t.get("source") == "whale_buy")
        dex_count   = len(enriched) - pump_count - whale_count
        open_n      = len(state.get("open", []))

        # Top scored candidates — these went through all filters but didn't
        # cross the alert threshold. Shows exactly what the bot evaluated.
        near_misses = sorted(
            [t for t in scored if t.get("source") not in ("pump.fun", "whale_buy")],
            key=lambda x: x.get("score", 0), reverse=True
        )[:5]

        def _miss_reason(t):
            reasons = []
            if t["score"] < MIN_SCORE:
                reasons.append(f"score {t['score']}<{MIN_SCORE}")
            if t.get("verdict") == "AVOID":
                reasons.append("verdict=AVOID")
            liq = t.get("liquidity_usd") or 0
            if liq < MIN_LIQUIDITY:
                reasons.append(f"liq ${liq:,.0f}")
            age = t.get("pair_age_minutes")
            if age and age > MAX_AGE_MINUTES:
                reasons.append(f"age {age:.0f}m")
            return ", ".join(reasons) if reasons else "rug check"

        if minute < 35:   # full summary on first scan of each hour window
            top_lines = [
                f"  • <b>{t.get('symbol','?')}</b> ({t.get('chain','?').upper()})  "
                f"score {t.get('score',0)}  1h:{t.get('price_change_h1') or 0:+.0f}%  "
                f"⚠ {_miss_reason(t)}"
                for t in near_misses
            ]
            body = (
                f"💓 <b>Signal Scout v5</b>  —  {now}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"DEX: {dex_count}  ·  🔥 Pump: {pump_count}  ·  🐋 Whale: {whale_count}\n"
                f"Candidates: {len(fresh)}  scored  ·  📝 Open: {open_n}\n\n"
                f"─── Near misses (filtered out) ─\n" +
                ("\n".join(top_lines) if top_lines else "  —  quiet market, nothing scored")
            )
            tg_send(tg_token, tg_chat, body)
        else:
            tg_send(tg_token, tg_chat,
                f"🔍 {now}  |  Candidates: {len(fresh)}  |  📝 Open: {open_n}  |  No new signals"
            )

    # ── Save state ─────────────────────────────────────────────────────────────
    save_state(state)


if __name__ == "__main__":
    main()
