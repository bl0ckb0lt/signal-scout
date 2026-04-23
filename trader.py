#!/usr/bin/env python3
"""
Signal Scout — Jupiter auto-trader for Solana.

TRADE_MODE (set via GitHub Actions secret or env var):
  "paper"  — log only, no real money (default & safe)
  "semi"   — send Telegram alert "ready to buy", waits for /approve <symbol>
  "auto"   — executes trades automatically with hard risk limits

RISK CONTROLS (all configurable via env vars):
  MAX_TRADE_SOL      = 0.10  SOL per trade
  MAX_DAILY_LOSS_SOL = 0.50  SOL — kills auto mode if exceeded
  MAX_CONCURRENT     = 5     max open real trades at once
  MIN_SCORE_FOR_TRADE= 65    minimum signal score before trading
  MAX_BUY_SLIPPAGE   = 100   bps = 1% (tight for memes)
  STALE_MINUTES      = 10    skip buy if price moved >40% since signal
"""

import os, json, subprocess, base64, time, datetime

# ── Config (override via env) ─────────────────────────────────────────────────

TRADE_MODE         = os.environ.get("TRADE_MODE",          "paper")
WALLET_PRIVKEY_B58 = os.environ.get("SOLANA_PRIVATE_KEY",  "")   # base58 private key
MAX_TRADE_SOL      = float(os.environ.get("MAX_TRADE_SOL",      "0.10"))
MAX_DAILY_LOSS_SOL = float(os.environ.get("MAX_DAILY_LOSS_SOL", "0.50"))
MAX_CONCURRENT     = int(os.environ.get("MAX_CONCURRENT",       "5"))
MIN_SCORE          = int(os.environ.get("MIN_SCORE_FOR_TRADE",  "65"))
MAX_SLIPPAGE_BPS   = int(os.environ.get("MAX_BUY_SLIPPAGE",     "100"))
STALE_MINUTES      = int(os.environ.get("STALE_MINUTES",        "10"))

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS = 1_000_000_000          # 1 SOL = 1e9 lamports

REAL_TRADES_FILE = "real_trades.json"

# Risk constants (also re-exported for scan_and_alert check_real_exits)
STOP_LOSS_PCT      = 15.0
TRAIL_ACTIVATE_PCT = 15.0
TRAIL_PCT          = 10.0
HARD_TP_PCT        = 60.0


def tg_send(token, chat_id, text):
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
    subprocess.run([
        "curl", "-s", "-X", "POST",
        f"https://api.telegram.org/bot{token}/sendMessage",
        "-H", "Content-Type: application/json", "-d", payload,
    ], capture_output=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _curl(url, body=None, method="GET", headers=None, timeout=15):
    args = ["curl", "-s", "--max-time", str(timeout), url]
    for k, v in (headers or {}).items():
        args += ["-H", f"{k}: {v}"]
    if body:
        args += ["-X", "POST", "-H", "Content-Type: application/json",
                 "-d", json.dumps(body)]
    r = subprocess.run(args, capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8"))
    except Exception:
        return None


def load_real_trades():
    try:
        with open(REAL_TRADES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "open":             [],
            "closed":           [],
            "daily_loss_sol":   0.0,
            "daily_date":       "",
            "pending_approvals": {},
        }


def save_real_trades(rt):
    with open(REAL_TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(rt, f, indent=2)
    subprocess.run(["git", "config", "user.email", "signalscout@bot"], capture_output=True)
    subprocess.run(["git", "config", "user.name",  "Signal Scout Bot"], capture_output=True)
    subprocess.run(["git", "add", REAL_TRADES_FILE], capture_output=True)
    r = subprocess.run(["git", "commit", "-m", "chore: real trades [skip ci]"], capture_output=True)
    if b"nothing to commit" not in r.stdout + r.stderr:
        subprocess.run(["git", "push"], capture_output=True)


# ── Daily loss reset ──────────────────────────────────────────────────────────

def reset_daily_if_needed(rt):
    today = datetime.date.today().isoformat()
    if rt.get("daily_date") != today:
        rt["daily_loss_sol"] = 0.0
        rt["daily_date"]     = today
    return rt


# ── Jupiter quote ─────────────────────────────────────────────────────────────

def jupiter_quote(input_mint, output_mint, amount_lamports, slippage_bps=100):
    """
    Get best swap route from Jupiter.
    Returns quote dict or None.
    """
    url = (
        f"https://quote-api.jup.ag/v6/quote"
        f"?inputMint={input_mint}"
        f"&outputMint={output_mint}"
        f"&amount={amount_lamports}"
        f"&slippageBps={slippage_bps}"
        f"&onlyDirectRoutes=false"
        f"&asLegacyTransaction=false"
    )
    return _curl(url)


def jupiter_swap_tx(wallet_pubkey, quote):
    """
    Get the serialised transaction from Jupiter.
    Returns base64 transaction string or None.
    """
    body = {
        "quoteResponse":             quote,
        "userPublicKey":             wallet_pubkey,
        "wrapAndUnwrapSol":          True,
        "computeUnitPriceMicroLamports": "auto",
        "asLegacyTransaction":       False,
    }
    resp = _curl("https://quote-api.jup.ag/v6/swap", body=body)
    if not resp:
        return None, None
    return resp.get("swapTransaction"), resp.get("lastValidBlockHeight")


# ── Solana transaction signing & broadcast ────────────────────────────────────

def _load_keypair():
    """Load Solana keypair from base58 private key."""
    if not WALLET_PRIVKEY_B58:
        return None, None
    try:
        import base58 as b58
        raw = b58.b58decode(WALLET_PRIVKEY_B58)          # 64 bytes: privkey + pubkey
        from nacl.signing import SigningKey
        sk  = SigningKey(raw[:32])
        pk  = bytes(sk.verify_key)
        return sk, b58.b58encode(pk).decode()
    except ImportError:
        print("  ⚠  Install: pip install base58 PyNaCl")
        return None, None
    except Exception as ex:
        print(f"  ⚠  Keypair load error: {ex}")
        return None, None


def sign_and_send(tx_b64):
    """
    Sign a versioned transaction from Jupiter and broadcast to Solana.
    Returns (signature_str, error_str).
    """
    sk, pubkey = _load_keypair()
    if not sk:
        return None, "No keypair"

    try:
        import base58 as b58
        from nacl.signing import SigningKey

        raw_tx = base64.b64decode(tx_b64)

        # Versioned transaction layout:
        #   [0]  = 0x80 | version (version 0 → 0x80)
        #   message follows — we need to sign the message bytes
        # Jupiter returns a versioned tx; we sign the full message.

        # Prefix = number of required signatures (compact-u16, 1 byte for ≤127)
        num_sigs      = raw_tx[0]
        sig_section   = raw_tx[1 : 1 + num_sigs * 64]
        message_bytes = raw_tx[1 + num_sigs * 64:]

        signed = sk.sign(message_bytes)
        signature_bytes = bytes(signed.signature)

        # Replace placeholder signature
        new_tx = bytes([num_sigs]) + signature_bytes + bytes(sig_section[64:]) + message_bytes

        body = {
            "jsonrpc": "2.0", "id": 1,
            "method": "sendTransaction",
            "params": [
                base64.b64encode(new_tx).decode(),
                {"encoding": "base64", "skipPreflight": False,
                 "preflightCommitment": "confirmed",
                 "maxRetries": 3},
            ],
        }
        resp = _curl("https://api.mainnet-beta.solana.com", body=body)
        if not resp:
            return None, "No RPC response"
        err = resp.get("error")
        if err:
            return None, str(err)
        return resp.get("result"), None

    except Exception as ex:
        return None, str(ex)


# ── Price freshness check ─────────────────────────────────────────────────────

def current_price_solana(mint):
    """Fetch current token price from DexScreener."""
    data  = _curl(f"https://api.dexscreener.com/latest/dex/tokens/{mint}") or {}
    pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == "solana"]
    if not pairs:
        return None
    best  = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
    try:
        return float(best.get("priceUsd") or 0) or None
    except Exception:
        return None


# ── Core trade execution ──────────────────────────────────────────────────────

def execute_buy(token, signal_price, tg_token, tg_chat):
    """
    Execute a real buy via Jupiter.
    token: dict with at least 'address', 'symbol', 'chain', 'score'
    signal_price: price at the time of signal detection
    Returns: trade dict or None
    """

    mint  = token["address"]
    sym   = token["symbol"]
    chain = token["chain"]

    if chain != "solana":
        print(f"  [trader] Auto-trading only supports Solana (got {chain})")
        return None

    sk, pubkey = _load_keypair()
    if not sk or not pubkey:
        tg_send(tg_token, tg_chat,
            f"⚠️ <b>Auto-trade failed</b> — SOLANA_PRIVATE_KEY not set.\n"
            f"Set the secret in GitHub Actions → Secrets.")
        return None

    # ── Staleness check ────────────────────────────────────────────────────
    now_price = current_price_solana(mint)
    if signal_price and now_price:
        moved_pct = abs((now_price - signal_price) / signal_price * 100)
        if moved_pct > 40:
            print(f"  [trader] {sym} price moved {moved_pct:.0f}% since signal — skipping (stale)")
            tg_send(tg_token, tg_chat,
                f"⏭ <b>Skipped {sym}</b> — price already moved {moved_pct:.0f}% since signal.")
            return None

    # ── Get Jupiter quote ──────────────────────────────────────────────────
    amount_lam = int(MAX_TRADE_SOL * LAMPORTS)
    quote = jupiter_quote(SOL_MINT, mint, amount_lam, MAX_SLIPPAGE_BPS)
    if not quote or not quote.get("outAmount"):
        print(f"  [trader] No Jupiter route for {sym}")
        return None

    out_amount  = int(quote["outAmount"])
    price_impact = float(quote.get("priceImpactPct") or 0)

    if price_impact > 3.0:
        print(f"  [trader] {sym} price impact {price_impact:.1f}% too high — skip")
        tg_send(tg_token, tg_chat,
            f"⏭ <b>Skipped {sym}</b> — price impact {price_impact:.1f}% (>3%). Low liquidity.")
        return None

    # ── Get swap transaction ───────────────────────────────────────────────
    tx_b64, last_valid_bh = jupiter_swap_tx(pubkey, quote)
    if not tx_b64:
        print(f"  [trader] No swap tx for {sym}")
        return None

    # ── Sign & broadcast ───────────────────────────────────────────────────
    sig, err = sign_and_send(tx_b64)
    if err or not sig:
        print(f"  [trader] Tx error for {sym}: {err}")
        tg_send(tg_token, tg_chat,
            f"❌ <b>Trade failed</b>: {sym}\n<code>{err}</code>")
        return None

    exec_price = now_price or signal_price
    trade = {
        "symbol":       sym,
        "chain":        chain,
        "address":      mint,
        "score":        token.get("score", 0),
        "sol_spent":    MAX_TRADE_SOL,
        "tokens_out":   out_amount,
        "entry_price":  exec_price,
        "peak_price":   exec_price,
        "tx_sig":       sig,
        "entry_time":   datetime.datetime.now(datetime.UTC).isoformat(),
        "trailing_active": False,
        "status":       "open",
        # Whale TP target if signal came from whale_buy
        "whale_exit_tp": token.get("whale_exit_tp"),
    }

    tg_send(tg_token, tg_chat,
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  ✅ REAL BUY EXECUTED\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>{sym}</b>  ·  Score {token.get('score',0)}\n"
        f"    Spent   <b>{MAX_TRADE_SOL} SOL</b>\n"
        f"    Got     {out_amount:,} tokens\n"
        f"    Price   ${exec_price:.8f}\n"
        f"    Impact  {price_impact:.2f}%\n\n"
        f"🔗 <a href='https://solscan.io/tx/{sig}'>View on Solscan</a>\n"
        f"📋 <code>{mint}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    return trade


def execute_sell(trade, rt, tg_token, tg_chat):
    """
    Sell all tokens for a real trade back to SOL via Jupiter.
    Mutates rt: moves trade from open → closed.
    """

    sk, pubkey = _load_keypair()
    mint        = trade["address"]
    sym         = trade["symbol"]
    tokens_held = trade.get("tokens_out", 0)
    entry_p     = trade.get("entry_price") or 0

    if not sk or not pubkey:
        return

    if not tokens_held:
        return

    quote = jupiter_quote(mint, SOL_MINT, tokens_held, slippage_bps=200)
    if not quote or not quote.get("outAmount"):
        print(f"  [trader] No sell route for {sym}")
        return

    sol_back   = int(quote["outAmount"]) / LAMPORTS
    tx_b64, _  = jupiter_swap_tx(pubkey, quote)
    if not tx_b64:
        return

    sig, err = sign_and_send(tx_b64)
    if err or not sig:
        print(f"  [trader] Sell error {sym}: {err}")
        tg_send(tg_token, tg_chat, f"❌ <b>Sell failed</b>: {sym}\n<code>{err}</code>")
        return

    now_p    = current_price_solana(mint) or entry_p
    pnl_sol  = sol_back - trade["sol_spent"]
    pnl_pct  = (now_p - entry_p) / entry_p * 100 if entry_p else 0

    trade.update({
        "exit_price": now_p,
        "exit_pct":   round(pnl_pct, 2),
        "sol_back":   round(sol_back, 4),
        "pnl_sol":    round(pnl_sol, 4),
        "exit_sig":   sig,
        "exit_time":  datetime.datetime.now(datetime.UTC).isoformat(),
    })
    rt.setdefault("closed", []).append(trade)
    rt["open"] = [t for t in rt.get("open", []) if t["address"] != mint]

    if pnl_sol < 0:
        rt["daily_loss_sol"] = round(rt.get("daily_loss_sol", 0) + abs(pnl_sol), 4)

    icon = "✅" if pnl_pct >= 0 else "❌"
    tg_send(tg_token, tg_chat,
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {icon} REAL SELL EXECUTED\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>{sym}</b>  {pnl_pct:+.1f}%\n"
        f"    Spent    {trade['sol_spent']} SOL\n"
        f"    Received <b>{sol_back:.4f} SOL</b>\n"
        f"    P&L      <b>{pnl_sol:+.4f} SOL</b>\n\n"
        f"🔗 <a href='https://solscan.io/tx/{sig}'>View on Solscan</a>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


# ── Semi-auto: approval queue ─────────────────────────────────────────────────

def queue_for_approval(token, tg_token, tg_chat):
    """In SEMI mode, message the user asking for /approve <symbol>."""
    sym   = token["symbol"]
    price = token.get("price_usd", "?")
    score = token.get("score", 0)

    rt = load_real_trades()
    rt.setdefault("pending_approvals", {})[sym] = {
        "token":      token,
        "queued_at":  datetime.datetime.now(datetime.UTC).isoformat(),
    }
    save_real_trades(rt)

    tg_send(tg_token, tg_chat,
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  🟡 SEMI-AUTO APPROVAL\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>{sym}</b>  ·  Score {score}  ·  ${price}\n\n"
        f"Send <code>/approve {sym}</code> to execute real buy\n"
        f"({MAX_TRADE_SOL} SOL at market)\n"
        f"Offer expires in {STALE_MINUTES} min.\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


def handle_approve(sym, tg_token, tg_chat):
    """Called from commands.py when user sends /approve <sym>."""

    rt = load_real_trades()
    rt = reset_daily_if_needed(rt)
    pending = rt.get("pending_approvals", {})

    if sym.upper() not in [k.upper() for k in pending]:
        tg_send(tg_token, tg_chat, f"❓ No pending approval for <b>{sym}</b>.")
        return

    key = next(k for k in pending if k.upper() == sym.upper())
    pa  = pending.pop(key)
    token = pa["token"]

    # Check not expired
    queued = datetime.datetime.fromisoformat(pa["queued_at"])
    age_min = (datetime.datetime.now(datetime.UTC) - queued).total_seconds() / 60
    if age_min > STALE_MINUTES:
        tg_send(tg_token, tg_chat, f"⏰ Approval for <b>{sym}</b> expired ({age_min:.0f}m ago).")
        save_real_trades(rt)
        return

    # Risk checks
    if len(rt.get("open", [])) >= MAX_CONCURRENT:
        tg_send(tg_token, tg_chat,
            f"🚫 Max concurrent trades ({MAX_CONCURRENT}) reached. Close a position first.")
        save_real_trades(rt)
        return

    if rt.get("daily_loss_sol", 0) >= MAX_DAILY_LOSS_SOL:
        tg_send(tg_token, tg_chat,
            f"🚫 Daily loss limit ({MAX_DAILY_LOSS_SOL} SOL) reached. Trading halted for today.")
        save_real_trades(rt)
        return

    signal_price = float(token.get("price_usd") or 0) or None
    trade = execute_buy(token, signal_price, tg_token, tg_chat)
    if trade:
        rt.setdefault("open", []).append(trade)

    save_real_trades(rt)


# ── Main entry point (called from scan_and_alert.py) ─────────────────────────

def maybe_trade(token, tg_token, tg_chat):
    """
    Decide whether to trade based on TRADE_MODE and risk limits.
    Call this after a signal passes score + rug checks.
    """

    if TRADE_MODE == "paper":
        return  # handled by paper trading in scan_and_alert.py

    chain = token.get("chain", "")
    score = token.get("score", 0)

    if chain != "solana":
        return   # only Solana trading for now

    if score < MIN_SCORE:
        return

    rt = load_real_trades()
    rt = reset_daily_if_needed(rt)

    # Daily loss guard
    if rt.get("daily_loss_sol", 0) >= MAX_DAILY_LOSS_SOL:
        print(f"  [trader] Daily loss limit reached — no new trades today.")
        return

    # Concurrent limit
    if len(rt.get("open", [])) >= MAX_CONCURRENT:
        print(f"  [trader] Max concurrent trades ({MAX_CONCURRENT}) — skip.")
        return

    # Already tracking this token
    if any(t["address"] == token["address"] for t in rt.get("open", [])):
        return

    if TRADE_MODE == "semi":
        queue_for_approval(token, tg_token, tg_chat)

    elif TRADE_MODE == "auto":
        signal_price = float(token.get("price_usd") or 0) or None
        trade = execute_buy(token, signal_price, tg_token, tg_chat)
        if trade:
            rt.setdefault("open", []).append(trade)
            save_real_trades(rt)


def check_real_exits(tg_token, tg_chat):
    """
    Monitor real open positions for TP / SL.
    Uses same thresholds as paper trading.
    Call this from scan_and_alert.py main() alongside check_exits().
    """

    rt = load_real_trades()
    if not rt.get("open"):
        return

    rt = reset_daily_if_needed(rt)
    changed = False

    for trade in list(rt.get("open", [])):
        mint    = trade["address"]
        sym     = trade["symbol"]
        entry   = trade.get("entry_price") or 0

        if not entry:
            continue

        # Whale dynamic TP
        whale_tp = trade.get("whale_exit_tp")
        hard_tp  = whale_tp if whale_tp else HARD_TP_PCT

        now_p   = current_price_solana(mint)
        if not now_p:
            continue

        pct_entry = (now_p - entry) / entry * 100
        peak      = max(trade.get("peak_price") or entry, now_p)
        trail_stop = peak * (1 - TRAIL_PCT / 100)
        was_trailing = trade.get("trailing_active", False)
        trailing_now = pct_entry >= TRAIL_ACTIVATE_PCT

        trade["peak_price"]      = peak
        trade["trailing_active"] = trailing_now
        trade["current_pct"]     = round(pct_entry, 2)

        if trailing_now and not was_trailing:
            tg_send(tg_token, tg_chat,
                f"🔒 <b>{sym}</b> REAL trailing stop locked at +{pct_entry:.1f}%\n"
                f"Trail SL: ${trail_stop:.8f}")
            changed = True

        if pct_entry >= hard_tp:
            execute_sell(trade, rt, tg_token, tg_chat)
            changed = True
        elif trailing_now and now_p <= trail_stop:
            execute_sell(trade, rt, tg_token, tg_chat)
            changed = True
        elif not trailing_now and pct_entry <= -STOP_LOSS_PCT:
            execute_sell(trade, rt, tg_token, tg_chat)
            changed = True

        time.sleep(0.3)

    if changed:
        save_real_trades(rt)


def real_trade_summary():
    """Return formatted status of real trades for /real command."""
    rt     = load_real_trades()
    open_t = rt.get("open", [])
    closed = rt.get("closed", [])
    wins   = [t for t in closed if t.get("pnl_sol", 0) >= 0]
    losses = [t for t in closed if t.get("pnl_sol", 0) < 0]
    total_pnl = sum(t.get("pnl_sol", 0) for t in closed)
    daily_loss = rt.get("daily_loss_sol", 0)

    mode_badge = {"paper": "📝 Paper", "semi": "🟡 Semi-Auto", "auto": "🤖 Auto"}.get(TRADE_MODE, TRADE_MODE)

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"  💰 REAL TRADES — {mode_badge}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"─── Open ({len(open_t)}) ──────────────",
    ]
    for t in open_t:
        icon = "🔒" if t.get("trailing_active") else "⏳"
        lines.append(f"  {icon} <b>{t['symbol']}</b>  {t.get('current_pct',0):+.1f}%  ·  {t['sol_spent']} SOL")
    lines += [
        "",
        f"─── Closed ({len(closed)}) ─────────────",
        f"  ✅ Wins: {len(wins)}   ❌ Losses: {len(losses)}",
        f"  Total P&L: <b>{total_pnl:+.4f} SOL</b>",
        f"  Daily loss used: {daily_loss:.4f} / {MAX_DAILY_LOSS_SOL} SOL",
        "",
        f"─── Risk Limits ─────────────",
        f"  Per trade:  {MAX_TRADE_SOL} SOL",
        f"  Daily max loss: {MAX_DAILY_LOSS_SOL} SOL",
        f"  Max concurrent: {MAX_CONCURRENT}",
        f"  Min score: {MIN_SCORE}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)
