"""
birdeye.py — Birdeye new listings + trending scanner for Signal Scout.

Birdeye sees Solana new token listings and real-time trending before most
other feeds. Free API key at https://birdeye.so (no credit card needed).

Add to GitHub Secrets:
  BIRDEYE_API_KEY   ← from birdeye.so dashboard

Returns seed dicts with source='birdeye' for enrichment via enrich().
"""

import os, time, subprocess, json

BIRDEYE_BASE = "https://public-api.birdeye.so"


def _curl(url, api_key):
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "10",
             "-H", f"X-API-KEY: {api_key}",
             "-H", "accept: application/json",
             "-H", "x-chain: solana",
             url],
            capture_output=True, text=True, timeout=15
        )
        return json.loads(r.stdout) if r.stdout.strip() else None
    except Exception:
        return None


def fetch_birdeye_tokens():
    """
    Pull new listings and trending tokens from Birdeye.
    Returns seed dicts with source='birdeye'.
    """
    api_key = os.getenv("BIRDEYE_API_KEY", "")
    if not api_key:
        print("  birdeye: BIRDEYE_API_KEY not set — skipping")
        return []

    seen   = set()
    tokens = []

    # ① New listings — tokens listed in last 2h on Solana
    new_data = _curl(
        f"{BIRDEYE_BASE}/defi/v2/tokens/new_listing"
        f"?limit=20&meme_platform_enabled=true",
        api_key
    )
    items = (new_data or {}).get("data", {}).get("items") or []
    for item in items:
        addr = item.get("address") or ""
        if not addr or addr in seen:
            continue
        seen.add(addr)
        tokens.append({
            "chain":             "solana",
            "address":           addr,
            "symbol":            item.get("symbol") or "?",
            "name":              item.get("name") or "",
            "source":            "birdeye",
            "birdeye_type":      "new_listing",
            "birdeye_liquidity": item.get("liquidity") or 0,
            "birdeye_volume":    item.get("volume24h") or 0,
        })
    time.sleep(0.3)

    # ② Trending — top tokens by real trading activity (not paid boost)
    trend_data = _curl(
        f"{BIRDEYE_BASE}/defi/trending_tokens/v2"
        f"?sort_by=rank&sort_type=asc&offset=0&limit=20",
        api_key
    )
    items = (trend_data or {}).get("data", {}).get("items") or []
    for item in items:
        addr = item.get("address") or ""
        if not addr or addr in seen:
            continue
        seen.add(addr)
        tokens.append({
            "chain":             "solana",
            "address":           addr,
            "symbol":            item.get("symbol") or "?",
            "name":              item.get("name") or "",
            "source":            "birdeye",
            "birdeye_type":      "trending",
            "birdeye_liquidity": item.get("liquidity") or 0,
            "birdeye_volume":    item.get("volume24h") or 0,
        })

    print(f"  Birdeye candidates: {len(tokens)}")
    return tokens
