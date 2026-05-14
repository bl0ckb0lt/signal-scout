"""
gmgn.py — GMGN trending token scanner for Signal Scout.

GMGN shows tokens gaining traction on Solana before they hit DexScreener
boost/profile feeds. No API key needed — public endpoint.

Returns seed dicts with source='gmgn' for enrichment via enrich().
"""

import time, subprocess, json

GMGN_ENDPOINTS = [
    # Top by swaps in last 1h — freshest movers
    "https://gmgn.ai/defi/quotation/v1/rank/sol/swaps/1h?orderby=swaps&direction=desc&limit=20&filters[]=not_honeypot",
    # Top by volume in last 1h
    "https://gmgn.ai/defi/quotation/v1/rank/sol/swaps/1h?orderby=volume&direction=desc&limit=20&filters[]=not_honeypot",
]

def _curl(url):
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "10",
             "-H", "User-Agent: Mozilla/5.0",
             "-H", "Accept: application/json",
             url],
            capture_output=True, text=True, timeout=15
        )
        return json.loads(r.stdout) if r.stdout.strip() else None
    except Exception:
        return None


def fetch_gmgn_tokens():
    """
    Pull trending Solana tokens from GMGN.
    Deduplicates across endpoints, returns seed dicts for enrichment.
    """
    seen  = set()
    tokens = []

    for url in GMGN_ENDPOINTS:
        data = _curl(url)
        if not data:
            continue

        # GMGN wraps results in data.rank or data.data
        items = (data.get("data") or {}).get("rank") or data.get("rank") or []
        if not items and isinstance(data, list):
            items = data

        for item in items:
            addr = item.get("address") or item.get("mint") or item.get("token_address") or ""
            if not addr or addr in seen:
                continue
            seen.add(addr)
            tokens.append({
                "chain":   "solana",
                "address": addr,
                "symbol":  item.get("symbol") or item.get("name") or "?",
                "name":    item.get("name") or "",
                "source":  "gmgn",
                "gmgn_swaps_1h": item.get("swaps") or item.get("swaps1h") or 0,
                "gmgn_volume_1h": item.get("volume") or item.get("volume1h") or 0,
            })
        time.sleep(0.2)

    print(f"  GMGN trending candidates: {len(tokens)}")
    return tokens
