"""
Formats scored tokens into a readable report and optionally posts to Moltbook m/buildx.
"""

import json
import datetime
import subprocess


def format_report(scored_tokens: list[dict], top_n: int = 10) -> str:
    """Build a markdown report of the top signals."""
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    top = scored_tokens[:top_n]

    buys  = [t for t in top if t.get("verdict") == "BUY"]
    watch = [t for t in top if t.get("verdict") == "WATCH"]

    lines = [
        f"# Signal Scout Report — {now}",
        f"*Scanned {len(scored_tokens)} tokens across Solana, ETH, BSC, Base, Arbitrum, X Layer*",
        "",
        f"## 🟢 BUY Signals ({len(buys)} tokens)",
    ]

    for t in buys:
        lines += _format_token(t)

    lines += ["", f"## 🟡 WATCH ({len(watch)} tokens)"]
    for t in watch:
        lines += _format_token(t)

    lines += [
        "",
        "---",
        "*Data: DexScreener + OKX OnchainOS DEX | Scoring: Claude claude-sonnet-4-6*",
        "*Not financial advice. DYOR before entering any position.*",
    ]

    return "\n".join(lines)


def _format_token(t: dict) -> list[str]:
    score = t.get("score_total", 0)
    symbol = t.get("symbol", "?")
    chain = t.get("chain", "?").upper()
    price = t.get("price_usd")
    liq = t.get("liquidity_usd")
    vol24 = t.get("volume_h24")
    pc_h1 = t.get("price_change_h1")
    pc_h24 = t.get("price_change_h24")
    verdict = t.get("verdict", "")
    reasoning = t.get("reasoning", "")
    entry_note = t.get("entry_note", "")
    url = t.get("pair_url", "")

    price_str  = f"${float(price):.6f}" if price else "N/A"
    liq_str    = f"${liq:,.0f}" if liq else "N/A"
    vol_str    = f"${vol24:,.0f}" if vol24 else "N/A"
    pc_h1_str  = f"{pc_h1:+.1f}%" if pc_h1 is not None else "N/A"
    pc_h24_str = f"{pc_h24:+.1f}%" if pc_h24 is not None else "N/A"

    lines = [
        f"",
        f"### {symbol} ({chain}) — Score: {score}/100",
        f"**Price:** {price_str} | **Liq:** {liq_str} | **Vol 24h:** {vol_str}",
        f"**1h:** {pc_h1_str} | **24h:** {pc_h24_str}",
        f"**Signal:** {reasoning}",
    ]
    if entry_note:
        lines.append(f"**Entry:** {entry_note}")
    if url:
        lines.append(f"**Chart:** {url}")

    # Score breakdown
    breakdown = " | ".join([
        f"Mom:{t.get('score_momentum',0)}",
        f"Vol:{t.get('score_volume',0)}",
        f"Mkt:{t.get('score_market_structure',0)}",
        f"Qual:{t.get('score_token_quality',0)}",
        f"Risk:{t.get('score_risk_adjusted',0)}",
    ])
    lines.append(f"*Breakdown: {breakdown}*")
    return lines


def post_to_moltbook(report: str, api_key: str, run_label: str = "") -> dict:
    """Post the signal report to Moltbook m/buildx."""
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    label = f" [{run_label}]" if run_label else ""
    title = f"ProjectSubmission XLayerArena - Signal Scout{label}: Multi-Chain Meme Token Signals {now}"

    payload = json.dumps({
        "submolt_name": "buildx",
        "title": title,
        "content": report
    })

    result = subprocess.run([
        "curl", "-s", "-X", "POST",
        "https://www.moltbook.com/api/v1/posts",
        "-H", f"Authorization: Bearer {api_key}",
        "-H", "Content-Type: application/json",
        "-d", payload
    ], capture_output=True)

    try:
        return json.loads(result.stdout.decode("utf-8"))
    except Exception:
        return {"error": result.stdout.decode("utf-8", errors="replace")}


def solve_verification(verification_code: str, challenge_text: str, api_key: str) -> dict:
    """Decode and solve a Moltbook math verification challenge."""
    import re

    # strip obfuscation: remove non-alphanumeric except spaces and basic chars
    clean = re.sub(r'[\[\]^/\-]', '', challenge_text)
    clean = re.sub(r'\s+', ' ', clean).strip().lower()

    # extract numbers
    numbers = re.findall(r'\b(\d+)\b', clean)
    if len(numbers) >= 2:
        a, b = int(numbers[0]), int(numbers[1])
        # detect operation from keywords
        if any(w in clean for w in ["plus", "add", "sum", "total", "and", "combined"]):
            answer = a + b
        elif any(w in clean for w in ["minus", "subtract", "less", "slower", "slow", "decrease", "fewer"]):
            answer = a - b
        elif any(w in clean for w in ["times", "multiply", "product"]):
            answer = a * b
        elif any(w in clean for w in ["divide", "split", "per", "each"]):
            answer = a / b if b != 0 else 0
        else:
            answer = a + b  # default guess
    else:
        answer = 0

    answer_str = f"{float(answer):.2f}"

    payload = json.dumps({"verification_code": verification_code, "answer": answer_str})
    result = subprocess.run([
        "curl", "-s", "-X", "POST",
        "https://www.moltbook.com/api/v1/verify",
        "-H", f"Authorization: Bearer {api_key}",
        "-H", "Content-Type: application/json",
        "-d", payload
    ], capture_output=True)

    try:
        return json.loads(result.stdout.decode("utf-8"))
    except Exception:
        return {"error": result.stdout.decode("utf-8", errors="replace")}
