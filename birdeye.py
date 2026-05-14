"""
birdeye.py — DexScreener trending scanner (replaces Birdeye — no API key needed).

Uses DexScreener's trending endpoint which shows tokens gaining real traction
across all chains, sorted by score. Catches movers GMGN and boost feeds miss.
"""

import time, subprocess, json

DEXSCREENER_TRENDING = "https://api.dexscreener.com/token-boosts/top/v1"
DEXSCREENER_LATEST   = "https://api.dexscreener.com/tokens/trending/v1"


def _curl(url):
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "10", "-H", "Accept: application/json", url],
            capture_output=True, text=True, timeout=15
        )
        return json.loads(r.stdout) if r.stdout.strip() else None
    except Exception:
        return None


def fetch_birdeye_tokens():
    """
    Pull trending tokens from DexScreener trending endpoint.
    Returns seed dicts with source='birdeye' (keeps existing wiring intact).
    """
    seen   = set()
    tokens = []

    data = _curl(DEXSCREENER_LATEST)
    items = data if isinstance(data, list) else (data or {}).get("pairs") or []

    for item in items:
        # trending endpoint returns pair objects
        token = item.get("baseToken") or {}
        addr  = token.get("address") or item.get("tokenAddress") or ""
        chain = item.get("chainId") or ""
        if not addr or not chain or addr in seen:
            continue
        seen.add(addr)
        tokens.append({
            "chain":          chain,
            "address":        addr,
            "symbol":         token.get("symbol") or "?",
            "name":           token.get("name") or "",
            "source":         "birdeye",
            "birdeye_type":   "trending",
            "birdeye_volume": float((item.get("volume") or {}).get("h24") or 0),
        })
        if len(tokens) >= 30:
            break

    print(f"  DexScreener trending candidates: {len(tokens)}")
    return tokens
