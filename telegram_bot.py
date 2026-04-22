"""
Telegram alert sender for Signal Scout.
Sends formatted token alerts to a Telegram chat.
"""

import json
import subprocess


def send_message(token: str, chat_id: str, text: str) -> bool:
    """Send a message to Telegram. Returns True on success."""
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
    r = subprocess.run([
        "curl", "-s", "-X", "POST",
        f"https://api.telegram.org/bot{token}/sendMessage",
        "-H", "Content-Type: application/json",
        "-d", payload
    ], capture_output=True)
    try:
        resp = json.loads(r.stdout.decode("utf-8"))
        return resp.get("ok", False)
    except Exception:
        return False


def format_alert(token: dict, is_new: bool = True) -> str:
    """Format a token signal into a Telegram HTML message."""
    verdict = token.get("verdict", "WATCH")
    score = token.get("score_total", 0)
    symbol = token.get("symbol", "?")
    chain = token.get("chain", "?").upper()
    price = token.get("price_usd")
    liq = token.get("liquidity_usd") or 0
    vol1h = token.get("volume_h1") or 0
    vol24 = token.get("volume_h24") or 0
    pc_m5 = token.get("price_change_m5")
    pc_h1 = token.get("price_change_h1")
    pc_h6 = token.get("price_change_h6")
    pc_h24 = token.get("price_change_h24")
    buys = token.get("buys_h1") or 0
    sells = token.get("sells_h1") or 0
    bsr = round(buys / sells, 2) if sells else 0
    age = token.get("pair_age_hours")
    dex = token.get("dex_id", "?")
    reasoning = token.get("reasoning", "")
    entry = token.get("entry_note", "")
    url = token.get("pair_url", "")
    source = token.get("source", "")

    verdict_emoji = {"BUY": "🟢", "WATCH": "🟡", "AVOID": "🔴"}.get(verdict, "⚪")
    new_tag = "🆕 <b>EARLY ALERT</b>" if is_new else "📊 <b>SIGNAL UPDATE</b>"

    age_str = f"{age:.1f}h old" if age is not None else "age unknown"
    price_str = f"${float(price):.6f}" if price else "N/A"
    liq_str = f"${liq:,.0f}"
    vol1h_str = f"${vol1h:,.0f}"

    def pct(v):
        return f"{v:+.1f}%" if v is not None else "N/A"

    lines = [
        f"{new_tag}",
        f"",
        f"{verdict_emoji} <b>{symbol}</b> ({chain}) — Score: <b>{score}/100</b> {verdict}",
        f"",
        f"💰 Price: {price_str}",
        f"💧 Liquidity: {liq_str}",
        f"📈 Vol 1h: {vol1h_str}",
        f"",
        f"📊 Price change:",
        f"  5m: {pct(pc_m5)} | 1h: {pct(pc_h1)}",
        f"  6h: {pct(pc_h6)} | 24h: {pct(pc_h24)}",
        f"",
        f"🔄 Buys/Sells (1h): {buys}/{sells} (BSR {bsr})",
        f"⏱ Age: {age_str} | DEX: {dex}",
        f"📡 Source: {source}",
    ]

    if reasoning:
        lines += ["", f"🧠 <i>{reasoning}</i>"]
    if entry:
        lines += [f"🎯 <b>Entry:</b> {entry}"]
    if url:
        lines += ["", f"📉 <a href='{url}'>View Chart</a>"]

    return "\n".join(lines)


def format_scan_summary(scored: list[dict], scan_number: int) -> str:
    """Format a compact scan summary for Telegram."""
    buys = [t for t in scored if t.get("verdict") == "BUY"]
    watches = [t for t in scored if t.get("verdict") == "WATCH"]

    lines = [
        f"🔍 <b>Signal Scout — Scan #{scan_number}</b>",
        f"",
        f"Scanned {len(scored)} tokens | "
        f"🟢 {len(buys)} BUY | 🟡 {len(watches)} WATCH",
        f"",
    ]

    top = sorted(scored, key=lambda t: t.get("score_total", 0), reverse=True)[:5]
    for t in top:
        v = t.get("verdict", "?")
        emoji = {"BUY": "🟢", "WATCH": "🟡", "AVOID": "🔴"}.get(v, "⚪")
        pc1 = t.get("price_change_h1")
        pc_str = f"{pc1:+.0f}%" if pc1 is not None else "N/A"
        age = t.get("pair_age_hours")
        age_str = f"{age:.1f}h" if age is not None else "?"
        lines.append(
            f"{emoji} <b>{t.get('symbol','?')}</b> ({t.get('chain','?').upper()}) "
            f"Score:{t.get('score_total',0)} | 1h:{pc_str} | {age_str} old"
        )

    return "\n".join(lines)
