#!/usr/bin/env python3
"""
Signal Scout — GitHub Actions runner.
Stateless scan: alerts only on tokens younger than MAX_AGE_MINUTES.
Runs every 10 min via GitHub Actions cron. No server needed.
"""

import os
import sys
import json
import time
import datetime
import subprocess
import hmac
import hashlib
import base64
import re
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

MAX_AGE_MINUTES  = 20     # only alert on tokens newer than this
MIN_SCORE        = 55     # minimum signal score to alert
MIN_LIQUIDITY    = 5000   # skip tokens with less liquidity than this
MAX_TOKENS       = 40     # cap tokens scored per run

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
        "-d", payload
    ], capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8")).get("ok", False)
    except Exception:
        return False


# ── OKX auth ─────────────────────────────────────────────────────────────────

def okx_headers(path, key, secret, passphrase):
    ts = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    sig = base64.b64encode(
        hmac.new(secret.encode(), (ts + "GET" + path).encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "OK-ACCESS-KEY": key,
        "OK-ACCESS-SIGN": sig,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_tokens(okx_key, okx_secret, okx_pass):
    tokens = {}

    # DexScreener boosted (paid marketing = strong signal)
    boosted = curl("https://api.dexscreener.com/token-boosts/top/v1") or []
    for t in boosted:
        k = (t.get("chainId",""), t.get("tokenAddress",""))
        if k[0] and k[1]:
            tokens[k] = {"chain": k[0], "address": k[1], "source": "boost",
                         "description": t.get("description","")}

    # DexScreener new profiles (freshly launched)
    profiles = curl("https://api.dexscreener.com/token-profiles/latest/v1") or []
    for t in profiles:
        k = (t.get("chainId",""), t.get("tokenAddress",""))
        if k[0] and k[1] and k not in tokens:
            tokens[k] = {"chain": k[0], "address": k[1], "source": "new",
                         "description": t.get("description","")}

    # X Layer via OKX OnchainOS
    path = "/api/v6/dex/aggregator/all-tokens?chainIndex=196"
    h = okx_headers(path, okx_key, okx_secret, okx_pass)
    xl = (curl("https://web3.okx.com" + path, h) or {}).get("data", [])
    for t in xl[:20]:
        addr = t.get("tokenContractAddress","")
        if addr and addr != "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE":
            k = ("xlayer", addr)
            tokens[k] = {"chain":"xlayer","address":addr,"source":"xlayer",
                         "symbol":t.get("tokenSymbol",""),"name":t.get("tokenName",""),
                         "description":""}

    return list(tokens.values())


def enrich(token):
    chain, addr = token["chain"], token["address"]
    if chain == "xlayer":
        return {**token, "pair_age_minutes": None, "liquidity_usd": 0,
                "volume_h1": None, "price_change_h1": None,
                "buys_h1": None, "sells_h1": None, "price_usd": None,
                "volume_h24": None, "price_change_h24": None,
                "fdv": None, "pair_url": None, "dex_id": "okx_xlayer",
                "symbol": token.get("symbol","?"), "name": token.get("name","?")}

    data = curl(f"https://api.dexscreener.com/latest/dex/tokens/{addr}") or {}
    pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == chain]
    if not pairs:
        return None

    p = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
    age_ms = p.get("pairCreatedAt")
    age_min = round((time.time() - age_ms / 1000) / 60, 1) if age_ms else None

    return {
        **token,
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
    }


# ── Score (rule-based, no API key needed) ─────────────────────────────────────

def score(t):
    s = 0
    liq  = t.get("liquidity_usd") or 0
    vol1 = t.get("volume_h1") or 0
    vol24= t.get("volume_h24") or 1
    pc1  = t.get("price_change_h1") or 0
    pc6  = t.get("price_change_h6") or 0
    pc24 = t.get("price_change_h24") or 0
    buys = t.get("buys_h1") or 0
    sells= t.get("sells_h1") or 1
    age  = t.get("pair_age_minutes")
    src  = t.get("source","")

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
    if bsr > 3:    s += 20
    elif bsr > 2:  s += 15
    elif bsr > 1.5:s += 10
    elif bsr > 1:  s += 5

    # Liquidity depth (0-15)
    if liq > 200000: s += 15
    elif liq > 50000:s += 10
    elif liq > 20000:s += 6
    elif liq > 5000: s += 3

    # Source / freshness bonus (0-15)
    if src == "boost": s += 8
    if age is not None and age < 60:  s += 7
    elif age is not None and age < 180: s += 3

    verdict = "BUY" if s >= 65 else "WATCH" if s >= 45 else "AVOID"
    return {**t, "score": s, "verdict": verdict}


# ── Rug check ─────────────────────────────────────────────────────────────────

CHAIN_IDS = {"ethereum":"1","bsc":"56","base":"8453","arbitrum":"42161"}

def rug_check(t):
    chain = t.get("chain","")
    addr  = t.get("address","")
    if chain == "solana":
        d = curl(f"https://api.rugcheck.xyz/v1/tokens/{addr}/report/summary") or {}
        score_val = d.get("score", 0)
        risks = [r.get("name","") for r in d.get("risks", [])]
        bad = any(r in ["Freeze Authority still enabled","Mint Authority still enabled"] for r in risks)
        return {"safe": not bad and score_val < 500, "detail": f"score {score_val}", "risks": risks[:3]}
    elif chain in CHAIN_IDS:
        d = curl(f"https://api.honeypot.is/v2/IsHoneypot?address={addr}&chainID={CHAIN_IDS[chain]}") or {}
        hp = (d.get("honeypotResult") or {}).get("isHoneypot", False)
        sell_tax = (d.get("simulationResult") or {}).get("sellTax", 0) or 0
        return {"safe": not hp and sell_tax <= 10, "detail": f"sell tax {sell_tax:.0f}%", "honeypot": hp}
    return {"safe": True, "detail": "unchecked"}


# ── Format alert ──────────────────────────────────────────────────────────────

def format_alert(t, rc):
    verdict = t.get("verdict","?")
    emoji = {"BUY":"🟢","WATCH":"🟡","AVOID":"🔴"}.get(verdict,"⚪")
    sym   = t.get("symbol","?")
    chain = t.get("chain","?").upper()
    sc    = t.get("score",0)
    price = t.get("price_usd")
    liq   = t.get("liquidity_usd") or 0
    vol1  = t.get("volume_h1") or 0
    pc1   = t.get("price_change_h1")
    pc24  = t.get("price_change_h24")
    buys  = t.get("buys_h1") or 0
    sells = t.get("sells_h1") or 0
    bsr   = round(buys/sells,2) if sells else 0
    age   = t.get("pair_age_minutes")
    url   = t.get("pair_url","")
    src   = t.get("source","")

    age_str   = f"{age:.0f}m old" if age is not None else "age unknown"
    price_str = f"${float(price):.6f}" if price else "N/A"
    pc1_str   = f"{pc1:+.1f}%" if pc1 is not None else "N/A"
    pc24_str  = f"{pc24:+.1f}%" if pc24 is not None else "N/A"
    rug_str   = "✅ Safe" if rc.get("safe") else ("🚨 DANGER" if rc.get("honeypot") else f"⚠️ {rc.get('detail','?')}")

    lines = [
        f"🆕 <b>EARLY SIGNAL — {age_str}</b>",
        f"",
        f"{emoji} <b>{sym}</b> ({chain}) — Score <b>{sc}/100</b> {verdict}",
        f"",
        f"💰 {price_str}  |  💧 ${liq:,.0f} liq",
        f"📈 Vol 1h: ${vol1:,.0f}",
        f"1h: {pc1_str}  |  24h: {pc24_str}",
        f"Buys/Sells: {buys}/{sells} (BSR {bsr})",
        f"📡 {src}  |  🛡 {rug_str}",
    ]
    if url:
        lines.append(f"\n📉 <a href='{url}'>View Chart</a>")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    tg_token  = os.environ["TELEGRAM_BOT_TOKEN"]
    tg_chat   = os.environ["TELEGRAM_CHAT_ID"]
    okx_key   = os.environ["OKX_API_KEY"]
    okx_sec   = os.environ["OKX_SECRET_KEY"]
    okx_pass  = os.environ["OKX_PASSPHRASE"]

    now = datetime.datetime.utcnow().strftime("%H:%M UTC")
    print(f"[{now}] Signal Scout scan starting...")

    # Fetch
    raw = fetch_tokens(okx_key, okx_sec, okx_pass)
    print(f"  Fetched {len(raw)} candidates")

    # Enrich
    enriched = []
    for t in raw:
        try:
            e = enrich(t)
            if e:
                enriched.append(e)
            time.sleep(0.05)
        except Exception as ex:
            print(f"  enrich error {t.get('address','?')[:10]}: {ex}")

    print(f"  Enriched {len(enriched)}")

    # Filter: only fresh tokens under MAX_AGE_MINUTES, enough liquidity
    fresh = [t for t in enriched
             if (t.get("pair_age_minutes") or 999) <= MAX_AGE_MINUTES
             and (t.get("liquidity_usd") or 0) >= MIN_LIQUIDITY]
    print(f"  Fresh (< {MAX_AGE_MINUTES}m, liq > ${MIN_LIQUIDITY}): {len(fresh)}")

    # Score
    scored = [score(t) for t in fresh]
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Alert on strong signals
    alerts_sent = 0
    for t in scored[:MAX_TOKENS]:
        if t["score"] < MIN_SCORE or t["verdict"] == "AVOID":
            continue

        rc = rug_check(t)
        if not rc.get("safe"):
            print(f"  Skipped (rug): {t.get('symbol')} — {rc}")
            continue

        msg = format_alert(t, rc)
        ok = tg_send(tg_token, tg_chat, msg)
        if ok:
            alerts_sent += 1
            print(f"  Alert sent: {t.get('symbol')} score={t['score']} age={t.get('pair_age_minutes')}m")
        time.sleep(1)

    print(f"  Done. {alerts_sent} alerts sent from {len(scored)} scored tokens.")

    # Send a heartbeat every hour (when no alerts and run count is on the hour)
    if alerts_sent == 0:
        minute = datetime.datetime.utcnow().minute
        if minute < 10:  # roughly every hour
            tg_send(tg_token, tg_chat,
                f"💓 Signal Scout running — {now}\n"
                f"Scanned {len(enriched)} tokens, {len(fresh)} fresh.\n"
                f"No strong signals this window.")


if __name__ == "__main__":
    main()
