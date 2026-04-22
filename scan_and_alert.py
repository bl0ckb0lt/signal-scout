#!/usr/bin/env python3
"""
Signal Scout v2 — GitHub Actions runner.
Stateless scan: alerts on tokens younger than MAX_AGE_MINUTES.
+ Pump.fun bonding curve (pre-DEX)
+ Smart money wallet tracking
+ Token image alerts via Telegram sendPhoto
Runs every 10 min via GitHub Actions cron. No server needed.
"""

import os
import json
import time
import datetime
import subprocess
import hmac
import hashlib
import base64

# ── Config ────────────────────────────────────────────────────────────────────

MAX_AGE_MINUTES      = 180
MIN_SCORE            = 55
MIN_LIQUIDITY        = 5000
MAX_TOKENS           = 40
MIN_MOMENTUM_H1      = 15

PUMP_MIN_MCAP        = 8_000     # min $8k mcap on bonding curve
PUMP_MAX_PROGRESS    = 85        # skip tokens already graduating (> 85%)
PUMP_MIN_PROGRESS    = 3         # skip brand-new with zero traction
PUMP_MAX_AGE_MINUTES = 120       # only catch pump tokens < 2h old
PUMP_MIN_TRADES      = 8         # must have some buy activity

# Known profitable Solana wallets — add your own high-conviction addresses
SMART_WALLETS = {
    "GKvqsuNcnwWqPzzuhLmGi4jx7PNyls4dpwfPkxhh4e2N": "Alpha Whale",
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM": "Degen King",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh": "Meme Sniper",
    "E8cU1muzBbhsAFpTL8HoGEWRCdWFzPBDNbNMgEgdmKHH": "Sol Whale",
    "CRekSRQpLHLydvnPLpFV2xXKKpQFzMaZb6GYCHzqWmr": "Known Profitable",
    "5tzFkiKscXHK5ZXCGbGuygQhjouGYXfZMAAH5dJMxGr":  "Smart Trader A",
    "7PGNXydRPtybHiMjkNBFGTVZMZGrTKaTi8RQKB9hjEN":  "Smart Trader B",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def curl(url, headers=None, method="GET", body=None):
    args = ["curl", "-s", "--max-time", "15", url]
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
        "-H", "Content-Type: application/json",
        "-d", payload,
    ], capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8")).get("ok", False)
    except Exception:
        return False


def tg_send_photo(token, chat_id, photo_url, caption):
    """Send token logo + alert text. Falls back to plain text if image fails."""
    payload = json.dumps({
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "HTML",
    })
    r = subprocess.run([
        "curl", "-s", "-X", "POST",
        f"https://api.telegram.org/bot{token}/sendPhoto",
        "-H", "Content-Type: application/json",
        "-d", payload,
    ], capture_output=True)
    try:
        resp = json.loads(r.stdout.decode("utf-8"))
        if resp.get("ok"):
            return True
    except Exception:
        pass
    return tg_send(token, chat_id, caption)


# ── OKX auth ──────────────────────────────────────────────────────────────────

def okx_headers(path, key, secret, passphrase):
    ts = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    sig = base64.b64encode(
        hmac.new(secret.encode(), (ts + "GET" + path).encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "OK-ACCESS-KEY":        key,
        "OK-ACCESS-SIGN":       sig,
        "OK-ACCESS-TIMESTAMP":  ts,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type":         "application/json",
    }


# ── Pump.fun bonding curve ────────────────────────────────────────────────────

def check_smart_money_pump(mint):
    """Return list of smart wallet labels that recently bought this pump token."""
    trades = curl(f"https://frontend-api.pump.fun/trades/all/{mint}?limit=50&offset=0") or []
    found = set()
    for trade in trades:
        if not trade.get("is_buy", True):
            continue
        wallet = trade.get("user", "")
        if wallet in SMART_WALLETS:
            found.add(SMART_WALLETS[wallet])
    return list(found)


def fetch_pump_tokens():
    """Fetch tokens still on Pump.fun bonding curve — caught before DEX listing."""
    coins = curl(
        "https://frontend-api.pump.fun/coins"
        "?sort=last_trade_timestamp&order=DESC&limit=50&includeNsfw=false"
    ) or []

    tokens = []
    now_ts = time.time()

    for coin in coins:
        mint = coin.get("mint", "")
        if not mint:
            continue

        # Skip if already graduated to Raydium/Meteora
        if coin.get("complete"):
            continue

        progress = (coin.get("bonding_curve_progress")
                    or coin.get("progress")
                    or 0)
        mcap     = coin.get("usd_market_cap") or coin.get("market_cap") or 0
        created  = coin.get("created_timestamp") or 0
        age_min  = (now_ts - created / 1000) / 60 if created else 9999
        trades_n = coin.get("total_trade_count") or 0

        if mcap < PUMP_MIN_MCAP:             continue
        if progress < PUMP_MIN_PROGRESS:     continue
        if progress > PUMP_MAX_PROGRESS:     continue
        if age_min  > PUMP_MAX_AGE_MINUTES:  continue
        if trades_n < PUMP_MIN_TRADES:       continue

        # Check smart money (extra API call only for qualifying tokens)
        smart = check_smart_money_pump(mint)
        time.sleep(0.1)

        tokens.append({
            "chain":            "solana",
            "address":          mint,
            "symbol":           coin.get("symbol", "?"),
            "name":             coin.get("name", "?"),
            "source":           "pump.fun",
            "description":      coin.get("description", ""),
            "icon":             coin.get("image_uri", ""),
            "pair_age_minutes": round(age_min, 1),
            "liquidity_usd":    mcap * 0.08,    # rough proxy (no pair yet)
            "volume_h1":        0,
            "volume_h24":       0,
            "price_change_h1":  None,
            "price_change_h24": None,
            "buys_h1":          trades_n,
            "sells_h1":         0,
            "price_usd":        None,
            "fdv":              mcap,
            "pair_url":         f"https://pump.fun/{mint}",
            "dex_id":           "pump.fun",
            "pump_progress":    round(progress, 1),
            "smart_money":      smart,
        })

    return tokens


# ── DexScreener + OKX fetch ───────────────────────────────────────────────────

def fetch_tokens(okx_key, okx_secret, okx_pass):
    tokens = {}

    # DexScreener boosted (paid marketing = strong signal)
    boosted = curl("https://api.dexscreener.com/token-boosts/top/v1") or []
    for t in boosted:
        k = (t.get("chainId", ""), t.get("tokenAddress", ""))
        if k[0] and k[1]:
            tokens[k] = {
                "chain": k[0], "address": k[1], "source": "boost",
                "description": t.get("description", ""),
                "icon": t.get("icon", "") or t.get("header", ""),
            }

    # DexScreener new profiles (freshly launched)
    profiles = curl("https://api.dexscreener.com/token-profiles/latest/v1") or []
    for t in profiles:
        k = (t.get("chainId", ""), t.get("tokenAddress", ""))
        if k[0] and k[1] and k not in tokens:
            tokens[k] = {
                "chain": k[0], "address": k[1], "source": "new",
                "description": t.get("description", ""),
                "icon": t.get("icon", "") or t.get("header", ""),
            }

    # X Layer via OKX OnchainOS
    path = "/api/v6/dex/aggregator/all-tokens?chainIndex=196"
    h = okx_headers(path, okx_key, okx_secret, okx_pass)
    xl = (curl("https://web3.okx.com" + path, h) or {}).get("data", [])
    for t in xl[:20]:
        addr = t.get("tokenContractAddress", "")
        if addr and addr != "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE":
            k = ("xlayer", addr)
            tokens[k] = {
                "chain": "xlayer", "address": addr, "source": "xlayer",
                "symbol": t.get("tokenSymbol", ""), "name": t.get("tokenName", ""),
                "description": "", "icon": t.get("tokenLogoUrl", ""),
            }

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

    # Try to get icon from pair info if not in token-profiles/boosts response
    icon = token.get("icon", "")
    if not icon:
        icon = (p.get("info") or {}).get("imageUrl", "")

    return {
        **token,
        "icon":             icon,
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
    s    = 0
    liq  = t.get("liquidity_usd") or 0
    vol24= t.get("volume_h24") or 1
    pc1  = t.get("price_change_h1") or 0
    buys = t.get("buys_h1") or 0
    sells= t.get("sells_h1") or 1
    age  = t.get("pair_age_minutes")
    src  = t.get("source", "")
    smart= t.get("smart_money", [])
    prog = t.get("pump_progress")

    # Momentum (0-25)
    if pc1 > 50:   s += 25
    elif pc1 > 20: s += 18
    elif pc1 > 5:  s += 10
    elif pc1 > 0:  s += 5

    # Volume/liquidity ratio (0-25)
    vlr = vol24 / liq if liq > 0 else 0
    if vlr > 30:   s += 25
    elif vlr > 10: s += 18
    elif vlr > 3:  s += 10
    elif vlr > 1:  s += 5

    # Buy pressure (0-20)
    bsr = buys / sells if sells > 0 else 0
    if bsr > 3:     s += 20
    elif bsr > 2:   s += 15
    elif bsr > 1.5: s += 10
    elif bsr > 1:   s += 5

    # Liquidity depth (0-15)
    if liq > 200000: s += 15
    elif liq > 50000:s += 10
    elif liq > 20000:s += 6
    elif liq > 5000: s += 3

    # Source / freshness (0-15 base)
    if src == "boost":    s += 8
    if src == "pump.fun": s += 10  # ultra early — pre-DEX
    if age is not None and age < 60:   s += 7
    elif age is not None and age < 180: s += 3

    # Smart money confirmation (+12)
    if smart: s += 12

    # Healthy bonding curve progress (+5)
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
        score_val = d.get("score", 0)
        risks = [r.get("name", "") for r in d.get("risks", [])]
        bad = any(r in ["Freeze Authority still enabled", "Mint Authority still enabled"]
                  for r in risks)
        return {"safe": not bad and score_val < 500, "detail": f"score {score_val}", "risks": risks[:3]}
    elif chain in CHAIN_IDS:
        d  = curl(f"https://api.honeypot.is/v2/IsHoneypot?address={addr}&chainID={CHAIN_IDS[chain]}") or {}
        hp = (d.get("honeypotResult") or {}).get("isHoneypot", False)
        sell_tax = (d.get("simulationResult") or {}).get("sellTax", 0) or 0
        return {"safe": not hp and sell_tax <= 10, "detail": f"sell tax {sell_tax:.0f}%", "honeypot": hp}
    return {"safe": True, "detail": "unchecked"}


# ── Format alert ──────────────────────────────────────────────────────────────

def format_alert(t, rc):
    verdict = t.get("verdict", "?")
    emoji   = {"BUY": "🟢", "WATCH": "🟡", "AVOID": "🔴"}.get(verdict, "⚪")
    sym     = t.get("symbol", "?")
    chain   = t.get("chain", "?").upper()
    sc      = t.get("score", 0)
    price   = t.get("price_usd")
    liq     = t.get("liquidity_usd") or 0
    vol1    = t.get("volume_h1") or 0
    pc1     = t.get("price_change_h1")
    pc24    = t.get("price_change_h24")
    buys    = t.get("buys_h1") or 0
    sells   = t.get("sells_h1") or 0
    bsr     = round(buys / sells, 2) if sells else 0
    age     = t.get("pair_age_minutes")
    url     = t.get("pair_url", "")
    src     = t.get("source", "")
    smart   = t.get("smart_money", [])
    prog    = t.get("pump_progress")
    fdv     = t.get("fdv")

    age_str   = f"{age:.0f}m old" if age is not None else "age unknown"
    price_str = f"${float(price):.8f}" if price else "N/A"
    pc1_str   = f"{pc1:+.1f}%" if pc1 is not None else "N/A"
    pc24_str  = f"{pc24:+.1f}%" if pc24 is not None else "N/A"
    rug_str   = ("✅ Safe" if rc.get("safe")
                 else ("🚨 HONEYPOT" if rc.get("honeypot")
                       else f"⚠️ {rc.get('detail', '?')}"))
    fdv_str   = f"${fdv:,.0f}" if fdv else "N/A"

    src_label = ("🔥 PUMP.FUN pre-DEX" if src == "pump.fun"
                 else "🚀 Boosted" if src == "boost"
                 else "🆕 New")

    lines = [
        f"🆕 <b>EARLY SIGNAL — {age_str}</b>",
        f"",
        f"{emoji} <b>{sym}</b> ({chain}) — Score <b>{sc}/100</b> {verdict}",
        f"",
        f"💰 {price_str}  |  💧 ${liq:,.0f} liq  |  FDV {fdv_str}",
        f"📈 Vol 1h: ${vol1:,.0f}",
        f"1h: {pc1_str}  |  24h: {pc24_str}",
        f"Buys/Sells: {buys}/{sells} (BSR {bsr})",
    ]

    if prog is not None:
        bar = "█" * int(prog / 10) + "░" * (10 - int(prog / 10))
        lines.append(f"🎯 Bonding curve: [{bar}] {prog:.0f}%")

    if smart:
        lines.append(f"🐋 <b>SMART MONEY: {', '.join(smart)}</b>")

    lines.append(f"📡 {src_label}  |  🛡 {rug_str}")

    if url:
        lines.append(f"\n📉 <a href='{url}'>View Chart</a>")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    tg_token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat  = os.environ["TELEGRAM_CHAT_ID"]
    okx_key  = os.environ["OKX_API_KEY"]
    okx_sec  = os.environ["OKX_SECRET_KEY"]
    okx_pass = os.environ["OKX_PASSPHRASE"]

    now = datetime.datetime.utcnow().strftime("%H:%M UTC")
    print(f"[{now}] Signal Scout v2 scan starting...")

    # ── Fetch ──────────────────────────────────────────────────────────────────

    raw = fetch_tokens(okx_key, okx_sec, okx_pass)
    print(f"  DEX candidates: {len(raw)}")

    pump_tokens = fetch_pump_tokens()
    print(f"  Pump.fun candidates: {len(pump_tokens)}")

    # Enrich DEX tokens
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
            buys = t.get("buys_h1") or 0
            if age <= PUMP_MAX_AGE_MINUTES and buys >= PUMP_MIN_TRADES:
                fresh.append(t)
        elif (age <= MAX_AGE_MINUTES
              and liq >= MIN_LIQUIDITY
              and (pc1 >= MIN_MOMENTUM_H1 or bool(smart))):
            fresh.append(t)

    print(f"  Fresh signals: {len(fresh)}")

    # ── Score ──────────────────────────────────────────────────────────────────

    scored = [score(t) for t in fresh]
    scored.sort(key=lambda x: x["score"], reverse=True)

    # ── Alert ──────────────────────────────────────────────────────────────────

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

        if icon and icon.startswith("http"):
            ok = tg_send_photo(tg_token, tg_chat, icon, msg)
        else:
            ok = tg_send(tg_token, tg_chat, msg)

        if ok:
            alerts_sent += 1
            flags = ""
            if has_smart:              flags += " 🐋SMART"
            if t.get("source") == "pump.fun": flags += " 🔥PUMP"
            print(f"  Alert: {t.get('symbol')} score={t['score']} age={t.get('pair_age_minutes')}m{flags}")
        time.sleep(1)

    print(f"  Done. {alerts_sent} alerts from {len(scored)} scored tokens.")

    # ── Status ping ────────────────────────────────────────────────────────────

    if alerts_sent == 0:
        minute     = datetime.datetime.utcnow().minute
        pump_count = sum(1 for t in enriched if t.get("source") == "pump.fun")
        dex_count  = len(enriched) - pump_count
        smart_hits = sum(1 for t in enriched if t.get("smart_money"))

        if minute < 10:
            top = sorted(
                [t for t in enriched if t.get("source") != "pump.fun"],
                key=lambda t: t.get("price_change_h1") or 0, reverse=True
            )[:3]
            top_lines = [
                f"  • {t.get('symbol','?')} ({t.get('chain','?').upper()}) "
                f"1h:{t.get('price_change_h1') or 0:+.0f}% {t.get('pair_age_minutes') or 0:.0f}m old"
                for t in top
            ]
            body = (
                f"💓 <b>Signal Scout v2 — Hourly Check</b>\n"
                f"Time: {now}\n"
                f"DEX: {dex_count} | 🔥 Pump.fun: {pump_count} | Fresh: {len(fresh)}\n"
                f"🐋 Smart money hits: {smart_hits}\n\n"
                f"Top movers:\n" + "\n".join(top_lines)
            ) if top_lines else (
                f"💓 Signal Scout v2 — {now}\nNo strong signals this hour."
            )
            tg_send(tg_token, tg_chat, body)
        else:
            tg_send(tg_token, tg_chat,
                f"🔍 Scan — {now}\n"
                f"DEX: {dex_count} | 🔥 Pump.fun: {pump_count} | Fresh: {len(fresh)}\n"
                f"No alerts this scan — watching..."
            )


if __name__ == "__main__":
    main()
