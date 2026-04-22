"""
Claude-powered token signal scorer.
Scores each token 0-100 across 5 dimensions, returns ranked list with reasoning.
"""

import json
import subprocess
from typing import Optional


ANTHROPIC_MODEL = "claude-sonnet-4-6"

SCORING_PROMPT = """You are a crypto signal analyst specializing in meme coins and early-stage tokens.
Analyze the following token data and score it for profit potential.

Score each dimension 0-20 (total max 100):

1. MOMENTUM (0-20): Price change trend across timeframes (m5, h1, h6, h24).
   Consistent upward momentum with acceleration = high score. Dumps or flat = low.

2. VOLUME_SIGNAL (0-20): Volume relative to liquidity, volume growth across timeframes.
   High volume / liquidity ratio with h1 > h6/6 = strong signal.

3. MARKET_STRUCTURE (0-20): Buy/sell ratio, liquidity depth, FDV reasonableness.
   More buys than sells, deep liquidity, low FDV = early opportunity.

4. TOKEN_QUALITY (0-20): Age of pair, source signal (boosted = marketing activity),
   description quality, chain ecosystem strength (X Layer = OKX backed, Solana = high liquidity).

5. RISK_ADJUSTED (0-20): Overall risk-reward. Penalize: no liquidity, brand-new with no volume,
   extreme FDV. Reward: reasonable entry point, verifiable activity.

Return ONLY valid JSON in this exact format:
{
  "scores": {
    "momentum": <0-20>,
    "volume_signal": <0-20>,
    "market_structure": <0-20>,
    "token_quality": <0-20>,
    "risk_adjusted": <0-20>
  },
  "total": <0-100>,
  "verdict": "<BUY|WATCH|AVOID>",
  "reasoning": "<2-3 sentences explaining the key signals>",
  "entry_note": "<brief note on entry timing or what to watch for>"
}"""


def _call_claude(prompt: str, token_data: str) -> Optional[dict]:
    """Call Claude API via subprocess (no SDK needed)."""
    import hmac, hashlib, base64, datetime, os

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    payload = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 512,
        "system": prompt,
        "messages": [{"role": "user", "content": token_data}]
    })

    result = subprocess.run([
        "curl", "-s", "-X", "POST",
        "https://api.anthropic.com/v1/messages",
        "-H", f"x-api-key: {api_key}",
        "-H", "anthropic-version: 2023-06-01",
        "-H", "content-type: application/json",
        "-d", payload
    ], capture_output=True, timeout=30)

    try:
        resp = json.loads(result.stdout.decode("utf-8"))
        text = resp["content"][0]["text"]
        # extract JSON from response
        start = text.find("{")
        end = text.rfind("}") + 1
        return json.loads(text[start:end])
    except Exception:
        return None


def score_token(token: dict) -> dict:
    """Score a single token using Claude. Returns token dict with score fields added."""
    # Build compact data summary for Claude
    age_hours = None
    if token.get("pair_created_at"):
        import time
        age_hours = round((time.time() - token["pair_created_at"] / 1000) / 3600, 1)

    buy_sell_ratio = None
    if token.get("buys_h1") and token.get("sells_h1") and token["sells_h1"] > 0:
        buy_sell_ratio = round(token["buys_h1"] / token["sells_h1"], 2)

    vol_liq_ratio = None
    if token.get("volume_h24") and token.get("liquidity_usd") and token["liquidity_usd"] > 0:
        vol_liq_ratio = round(token["volume_h24"] / token["liquidity_usd"], 2)

    data_str = f"""Token: {token.get('symbol', '?')} ({token.get('name', '?')})
Chain: {token.get('chain', '?')}
Source: {token.get('source', '?')}
Price USD: {token.get('price_usd')}
Liquidity USD: {token.get('liquidity_usd')}
FDV: {token.get('fdv')}
Volume 5m: {token.get('volume_h1')} | 1h: {token.get('volume_h1')} | 6h: {token.get('volume_h6')} | 24h: {token.get('volume_h24')}
Price change 5m: {token.get('price_change_m5')}% | 1h: {token.get('price_change_h1')}% | 6h: {token.get('price_change_h6')}% | 24h: {token.get('price_change_h24')}%
Buys/Sells 1h: {token.get('buys_h1')}/{token.get('sells_h1')} | 24h: {token.get('buys_h24')}/{token.get('sells_h24')}
Buy/Sell ratio 1h: {buy_sell_ratio}
Vol/Liq ratio: {vol_liq_ratio}
Pair age (hours): {age_hours}
DEX: {token.get('dex_id')}
Description: {token.get('description', '')[:120]}"""

    scored = _call_claude(SCORING_PROMPT, data_str)

    if scored:
        return {
            **token,
            "score_total": scored.get("total", 0),
            "score_momentum": scored["scores"].get("momentum", 0),
            "score_volume": scored["scores"].get("volume_signal", 0),
            "score_market_structure": scored["scores"].get("market_structure", 0),
            "score_token_quality": scored["scores"].get("token_quality", 0),
            "score_risk_adjusted": scored["scores"].get("risk_adjusted", 0),
            "verdict": scored.get("verdict", "WATCH"),
            "reasoning": scored.get("reasoning", ""),
            "entry_note": scored.get("entry_note", ""),
        }
    else:
        # fallback: rule-based score
        return _rule_based_score(token)


def _rule_based_score(token: dict) -> dict:
    """Fallback scorer when Claude is unavailable."""
    score = 0

    # Momentum
    pc_h1 = token.get("price_change_h1") or 0
    pc_h24 = token.get("price_change_h24") or 0
    if pc_h1 > 20: score += 15
    elif pc_h1 > 5: score += 10
    elif pc_h1 > 0: score += 5

    # Volume signal
    vol = token.get("volume_h24") or 0
    liq = token.get("liquidity_usd") or 1
    if vol / liq > 10: score += 18
    elif vol / liq > 3: score += 12
    elif vol / liq > 1: score += 6

    # Buy pressure
    buys = token.get("buys_h1") or 0
    sells = token.get("sells_h1") or 1
    if buys / sells > 2: score += 15
    elif buys / sells > 1.2: score += 10

    # Source bonus
    if token.get("source") == "boost": score += 10

    # Liquidity sanity
    if liq > 50000: score += 10
    elif liq > 10000: score += 5

    verdict = "BUY" if score >= 55 else "WATCH" if score >= 35 else "AVOID"
    return {**token, "score_total": score, "verdict": verdict,
            "reasoning": "Rule-based score (Claude unavailable).", "entry_note": ""}


def score_tokens(tokens: list[dict], top_n: int = 30) -> list[dict]:
    """Score up to top_n tokens, return sorted by score desc."""
    import time

    # Pre-filter: skip tokens with zero liquidity or volume
    viable = [t for t in tokens
              if (t.get("liquidity_usd") or 0) > 5000
              or t.get("chain") == "xlayer"]

    # Limit to top_n to control API usage
    viable = viable[:top_n]
    print(f"  Scoring {len(viable)} viable tokens with Claude...")

    scored = []
    for i, token in enumerate(viable):
        print(f"    [{i+1}/{len(viable)}] {token.get('symbol','?')} ({token.get('chain','?')})...")
        result = score_token(token)
        scored.append(result)
        time.sleep(0.3)  # rate limit

    return sorted(scored, key=lambda t: t.get("score_total", 0), reverse=True)
