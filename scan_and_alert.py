#!/usr/bin/env python3
"""
Signal Scout v4 — GitHub Actions runner.
+ Trailing stop loss  (locks in gains as price rises)
+ Hard take-profit cap at +60 %
+ Fixed stop loss before trailing activates
+ Paper trading, trade logger, bot commands, token images
Runs every 5 min via GitHub Actions cron. No server needed.
"""

import os, json, time, datetime, subprocess, hmac, hashlib, base64

# ── Config ────────────────────────────────────────────────────────────────────

MAX_AGE_MINUTES      = 180
MIN_SCORE            = 55
MIN_LIQUIDITY        = 5000
MAX_TOKENS           = 40
MIN_MOMENTUM_H1      = 15

PUMP_MIN_MCAP        = 8_000
PUMP_MAX_PROGRESS    = 85
PUMP_MIN_PROGRESS    = 3
PUMP_MAX_AGE_MINUTES = 120
PUMP_MIN_TRADES      = 8

# ── Paper trading ─────────────────────────────────────────────────────────────

PAPER_MODE          = True   # False = alerts only, no position tracking
STOP_LOSS_PCT       = 15.0   # fixed SL before trailing activates  (-15 %)
TRAIL_ACTIVATE_PCT  = 15.0   # start trailing once up this much     (+15 %)
TRAIL_PCT           = 10.0   # trail this far below the peak        (10 %)
HARD_TP_PCT         = 60.0   # hard exit — never let a winner fully reverse (+60 %)
MAX_OPEN_TRADES     = 10     # max positions tracked at once
PAPER_TRADES_FILE   = "paper_trades.json"

# ── Smart money wallets ───────────────────────────────────────────────────────

SMART_WALLETS = {
    "GKvqsuNcnwWqPzzuhLmGi4jx7PNyls4dpwfPkxhh4e2N": "Alpha Whale",
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM": "Degen King",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh": "Meme Sniper",
    "E8cU1muzBbhsAFpTL8HoGEWRCdWFzPBDNbNMgEgdmKHH": "Sol Whale",
    "5tzFkiKscXHK5ZXCGbGuygQhjouGYXfZMAAH5dJMxGr":  "Smart Trader A",
    "7PGNXydRPtybHiMjkNBFGTVZMZGrTKaTi8RQKB9hjEN":  "Smart Trader B",
}

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
            tg_send(tg_token, tg_chat, "▶️ <b>Signal Scout resumed.</b> Scanning every 5 min.")

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
                    entry   = p.get("entry_price") or 0
                    peak    = p.get("peak_price") or entry
                    pct_now = p.get("current_pct", 0)
                    trail   = peak * (1 - TRAIL_PCT / 100) if p.get("trailing_active") else None
                    t_icon  = "🔒" if p.get("trailing_active") else "⏳"
                    lines.append(
                        f"\n{t_icon} <b>{p['symbol']}</b> ({p['chain'].upper()})\n"
                        f"  Entry  ${entry:.8f}\n"
                        f"  Now    {pct_now:+.1f}%  ·  Score {p['score']}\n"
                        + (f"  Trail SL  ${trail:.8f}\n" if trail else "")
                        + f"  <code>{p['address']}</code>"
                    )
                lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━")
                tg_send(tg_token, tg_chat, "\n".join(lines))

        elif text in ("/help", "/start"):
            tg_send(tg_token, tg_chat,
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "  🤖 SIGNAL SCOUT v4\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "/status  — P&L, win rate, risk settings\n"
                "/trades  — open positions + trail stops\n"
                "/pause   — stop sending alerts\n"
                "/resume  — restart scanning\n\n"
                "─── Risk Management ────────\n"
                f"  Fixed SL:     -{STOP_LOSS_PCT:.0f}%\n"
                f"  Trail starts: +{TRAIL_ACTIVATE_PCT:.0f}%\n"
                f"  Trail gap:     {TRAIL_PCT:.0f}% from peak\n"
                f"  Hard TP:      +{HARD_TP_PCT:.0f}%\n"
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
    # Set cooldown so we don't re-enter this token immediately
    state.setdefault("cooldown", {})[pos["address"]] = now


def check_exits(state, tg_token, tg_chat):
    """
    Trailing stop engine — runs every 5 min scan.

    Flow per position:
      ① Price rises → update peak_price
      ② Once up TRAIL_ACTIVATE_PCT → switch to trailing mode (notify once)
      ③ While trailing: stop = peak × (1 - TRAIL_PCT/100)
         If price drops below stop → exit (TSL hit)
      ④ Hard cap: if up HARD_TP_PCT → exit immediately (hard TP)
      ⑤ Before trailing activates: fixed SL at -STOP_LOSS_PCT
    """
    still_open = []

    for pos in state.get("open", []):
        addr        = pos["address"]
        chain       = pos["chain"]
        src         = pos.get("source", "")
        entry       = pos.get("entry_price") or 0
        sym         = pos["symbol"]

        # Skip pump.fun / xlayer (no reliable price feed yet)
        if not entry or chain == "xlayer" or src == "pump.fun":
            still_open.append(pos)
            continue

        # ── Current price ──────────────────────────────────────────────────
        data  = curl(f"https://api.dexscreener.com/latest/dex/tokens/{addr}") or {}
        pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == chain]
        if not pairs:
            still_open.append(pos)
            continue
        p     = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
        now_p = float(p.get("priceUsd") or 0)
        if not now_p:
            still_open.append(pos)
            continue

        pct_entry = (now_p - entry) / entry * 100          # % from entry
        peak      = max(pos.get("peak_price") or entry, now_p)  # all-time peak
        pct_peak  = (now_p - peak) / peak * 100            # % from peak (≤ 0)
        trail_stop = peak * (1 - TRAIL_PCT / 100)
        was_trailing = pos.get("trailing_active", False)
        trailing_now = pct_entry >= TRAIL_ACTIVATE_PCT

        # Update peak
        pos["peak_price"]      = peak
        pos["trailing_active"] = trailing_now
        pos["current_pct"]     = round(pct_entry, 2)

        # ── ① Notify when trailing first kicks in ─────────────────────────
        if trailing_now and not was_trailing:
            tg_send(tg_token, tg_chat,
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  🔒 TRAILING STOP LOCKED\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>{sym}</b> ({chain.upper()})  up <b>+{pct_entry:.1f}%</b>\n\n"
                f"    Entry     ${entry:.8f}\n"
                f"    Peak      ${peak:.8f}\n"
                f"    Trail SL  ${trail_stop:.8f}  (-{TRAIL_PCT:.0f}% from peak)\n\n"
                f"🔒 Stop rises automatically as price climbs.\n"
                f"📋 <code>{addr}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━"
            )

        # ── ② Hard TP ─────────────────────────────────────────────────────
        if pct_entry >= HARD_TP_PCT:
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

        # ── ③ Trailing stop hit ───────────────────────────────────────────
        elif trailing_now and now_p <= trail_stop:
            gain_locked = round((trail_stop - entry) / entry * 100, 1)
            tg_send(tg_token, tg_chat,
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  🎯 TRAILING STOP HIT\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>{sym}</b> ({chain.upper()})\n\n"
                f"    Entry    ${entry:.8f}\n"
                f"    Peak     ${peak:.8f}  (+{(peak-entry)/entry*100:.1f}%)\n"
                f"    Exit     ${now_p:.8f}\n"
                f"    Locked   <b>+{gain_locked:.1f}%</b> profit ✅\n\n"
                f"🔒 Trailing stop protected your gains.\n"
                f"📋 <code>{addr}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            _close_pos(pos, now_p, "TSL", state)

        # ── ④ Fixed SL (before trailing activates) ────────────────────────
        elif not trailing_now and pct_entry <= -STOP_LOSS_PCT:
            tg_send(tg_token, tg_chat,
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  🛑 STOP LOSS HIT\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>{sym}</b> ({chain.upper()})  <b>{pct_entry:.1f}%</b>\n\n"
                f"    Entry  ${entry:.8f}\n"
                f"    Now    ${now_p:.8f}\n\n"
                f"❌ <b>Exit now</b> — protect your capital.\n"
                f"📋 <code>{addr}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            _close_pos(pos, now_p, "SL", state)

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
    return state


# ── OKX auth ──────────────────────────────────────────────────────────────────

def okx_headers(path, key, secret, passphrase):
    ts  = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    sig = base64.b64encode(
        hmac.new(secret.encode(), (ts + "GET" + path).encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "OK-ACCESS-KEY": key, "OK-ACCESS-SIGN": sig,
        "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }


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


# ── DexScreener + OKX fetch ───────────────────────────────────────────────────

def fetch_tokens(okx_key, okx_secret, okx_pass):
    tokens = {}
    boosted = curl("https://api.dexscreener.com/token-boosts/top/v1") or []
    for t in boosted:
        k = (t.get("chainId", ""), t.get("tokenAddress", ""))
        if k[0] and k[1]:
            tokens[k] = {"chain": k[0], "address": k[1], "source": "boost",
                         "description": t.get("description", ""),
                         "icon": t.get("icon", "") or t.get("header", "")}

    profiles = curl("https://api.dexscreener.com/token-profiles/latest/v1") or []
    for t in profiles:
        k = (t.get("chainId", ""), t.get("tokenAddress", ""))
        if k[0] and k[1] and k not in tokens:
            tokens[k] = {"chain": k[0], "address": k[1], "source": "new",
                         "description": t.get("description", ""),
                         "icon": t.get("icon", "") or t.get("header", "")}

    path = "/api/v6/dex/aggregator/all-tokens?chainIndex=196"
    h    = okx_headers(path, okx_key, okx_secret, okx_pass)
    xl   = (curl("https://web3.okx.com" + path, h) or {}).get("data", [])
    for t in xl[:20]:
        addr = t.get("tokenContractAddress", "")
        if addr and addr != "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE":
            k = ("xlayer", addr)
            tokens[k] = {"chain": "xlayer", "address": addr, "source": "xlayer",
                         "symbol": t.get("tokenSymbol", ""), "name": t.get("tokenName", ""),
                         "description": "", "icon": t.get("tokenLogoUrl", "")}
    return list(tokens.values())


def enrich(token):
    chain = token["chain"]
    addr  = token["address"]
    if chain == "xlayer":
        return {**token, "pair_age_minutes": None, "liquidity_usd": 0,
                "volume_h1": None, "price_change_h1": None,
                "buys_h1": None, "sells_h1": None, "price_usd": None,
                "volume_h24": None, "price_change_h24": None,
                "fdv": None, "pair_url": None, "dex_id": "okx_xlayer",
                "symbol": token.get("symbol", "?"), "name": token.get("name", "?"),
                "smart_money": [], "pump_progress": None}

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

    if src == "boost":    s += 8
    if src == "pump.fun": s += 10
    if smart:             s += 12
    if age is not None and age < 60:    s += 7
    elif age is not None and age < 180: s += 3
    if prog is not None and 20 <= prog <= 70: s += 5

    verdict = "BUY" if s >= 65 else "WATCH" if s >= 45 else "AVOID"
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
    prog    = t.get("pump_progress")
    fdv     = t.get("fdv")

    # ── Labels
    verdict_badge = {"BUY": "🟢 STRONG BUY", "WATCH": "🟡 WATCH", "AVOID": "🔴 AVOID"}.get(verdict, "⚪")
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
        f"⏱  Age: <b>{age_str}</b>  ·  Score: <b>{sc}/100</b>",
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

    if smart:
        lines += [
            f"",
            f"─── 🐋 SMART MONEY ─────────",
        ]
        for w in smart:
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
            f"    🛑 SL -{STOP_LOSS_PCT:.0f}%  →  🔒 Trail +{TRAIL_ACTIVATE_PCT:.0f}% (-{TRAIL_PCT:.0f}% peak)  →  🚀 TP +{HARD_TP_PCT:.0f}%",
        ]

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

def main():
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat  = os.environ["TELEGRAM_CHAT_ID"]
    okx_key  = os.environ["OKX_API_KEY"]
    okx_sec  = os.environ["OKX_SECRET_KEY"]
    okx_pass = os.environ["OKX_PASSPHRASE"]

    now = datetime.datetime.utcnow().strftime("%H:%M UTC")
    print(f"[{now}] Signal Scout v3 scan starting...")

    # ── Load state + poll commands ─────────────────────────────────────────────
    state = load_state()
    state = poll_commands(tg_token, tg_chat, state)

    if state.get("paused"):
        print("  Bot is paused — skipping scan.")
        save_state(state)
        return

    # ── Check open positions for TP / SL ──────────────────────────────────────
    if state.get("open"):
        print(f"  Checking {len(state['open'])} open positions...")
        state = check_exits(state, tg_token, tg_chat)

    # ── Fetch ──────────────────────────────────────────────────────────────────
    raw = fetch_tokens(okx_key, okx_sec, okx_pass)
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
    print(f"  Total enriched: {len(enriched)}")

    # ── Filter ─────────────────────────────────────────────────────────────────
    fresh = []
    for t in enriched:
        age   = t.get("pair_age_minutes") or 9999
        liq   = t.get("liquidity_usd") or 0
        pc1   = abs(t.get("price_change_h1") or 0)
        src   = t.get("source", "")
        smart = t.get("smart_money", [])
        if src == "pump.fun":
            if age <= PUMP_MAX_AGE_MINUTES and (t.get("buys_h1") or 0) >= PUMP_MIN_TRADES:
                fresh.append(t)
        elif (age <= MAX_AGE_MINUTES and liq >= MIN_LIQUIDITY
              and (pc1 >= MIN_MOMENTUM_H1 or bool(smart))):
            fresh.append(t)

    print(f"  Fresh signals: {len(fresh)}")

    # ── Score ──────────────────────────────────────────────────────────────────
    scored = [score(t) for t in fresh]
    scored.sort(key=lambda x: x["score"], reverse=True)

    # ── Alert + log ────────────────────────────────────────────────────────────
    alerts_sent = 0
    for t in scored[:MAX_TOKENS]:
        has_smart = bool(t.get("smart_money"))
        if t["score"] < MIN_SCORE and not has_smart:
            continue
        if t["verdict"] == "AVOID" and not has_smart:
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
            flags  = " 🐋" if has_smart else ""
            flags += " 🔥" if t.get("source") == "pump.fun" else ""
            print(f"  Alert: {t.get('symbol')} score={t['score']} age={t.get('pair_age_minutes')}m{flags}")
            if PAPER_MODE:
                state = log_paper_trade(state, t)
        time.sleep(1)

    print(f"  Done. {alerts_sent} alerts from {len(scored)} scored tokens.")

    # ── Status ping ────────────────────────────────────────────────────────────
    if alerts_sent == 0:
        minute     = datetime.datetime.utcnow().minute
        pump_count = sum(1 for t in enriched if t.get("source") == "pump.fun")
        dex_count  = len(enriched) - pump_count
        open_n     = len(state.get("open", []))

        if minute < 10:
            top = sorted(
                [t for t in enriched if t.get("source") != "pump.fun"],
                key=lambda t: t.get("price_change_h1") or 0, reverse=True
            )[:3]
            top_lines = [
                f"  • {t.get('symbol','?')} ({t.get('chain','?').upper()}) "
                f"1h:{t.get('price_change_h1') or 0:+.0f}% "
                f"{t.get('pair_age_minutes') or 0:.0f}m"
                for t in top
            ]
            body = (
                f"💓 <b>Signal Scout v3 — Hourly</b>\n"
                f"{now}  |  DEX: {dex_count}  |  🔥 Pump: {pump_count}\n"
                f"Fresh: {len(fresh)}  |  📝 Open trades: {open_n}\n\n"
                f"Top movers:\n" + "\n".join(top_lines)
            ) if top_lines else f"💓 Signal Scout v3 — {now}\nNo strong signals this hour."
            tg_send(tg_token, tg_chat, body)
        else:
            tg_send(tg_token, tg_chat,
                f"🔍 Scan — {now}\n"
                f"DEX: {dex_count}  |  🔥 Pump: {pump_count}  |  Fresh: {len(fresh)}\n"
                f"📝 Open trades: {open_n}  |  No new alerts"
            )

    # ── Save state ─────────────────────────────────────────────────────────────
    save_state(state)


if __name__ == "__main__":
    main()
