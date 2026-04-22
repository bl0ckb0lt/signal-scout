"""
Multi-chain token data fetcher.
Sources: DexScreener (Solana, ETH, BSC, Base) + OnchainOS DEX (X Layer + EVM chains)
"""

import hmac
import hashlib
import base64
import datetime
import json
import subprocess
import time
from typing import Optional


# ── helpers ──────────────────────────────────────────────────────────────────

def _curl(url: str, headers: dict = None, method="GET", body: str = None) -> dict | list | None:
    args = ["curl", "-s", "--max-time", "10", url]
    for k, v in (headers or {}).items():
        args += ["-H", f"{k}: {v}"]
    if body:
        args += ["-X", method, "-d", body]
    result = subprocess.run(args, capture_output=True)
    try:
        return json.loads(result.stdout.decode("utf-8"))
    except Exception:
        return None


# ── DexScreener ───────────────────────────────────────────────────────────────

DEXSCREENER_BASE = "https://api.dexscreener.com"

SUPPORTED_CHAINS = ["solana", "ethereum", "bsc", "base", "arbitrum", "avalanche"]


def dex_get_boosted_tokens() -> list[dict]:
    """Top 30 tokens with active marketing boosts — strong social signal."""
    data = _curl(f"{DEXSCREENER_BASE}/token-boosts/top/v1")
    return data if isinstance(data, list) else []


def dex_get_token_profiles() -> list[dict]:
    """Recently updated token profiles — new launches / active projects."""
    data = _curl(f"{DEXSCREENER_BASE}/token-profiles/latest/v1")
    return data if isinstance(data, list) else []


def dex_get_pairs_for_token(chain: str, address: str) -> list[dict]:
    """Full pair data for a token: price, volume, liquidity, txns, price change."""
    data = _curl(f"{DEXSCREENER_BASE}/latest/dex/tokens/{address}")
    if not data:
        return []
    pairs = data.get("pairs") or []
    return [p for p in pairs if p.get("chainId") == chain]


def dex_search_tokens(query: str, limit: int = 20) -> list[dict]:
    """Search for tokens by name/symbol across all chains."""
    data = _curl(f"{DEXSCREENER_BASE}/latest/dex/search?q={query}")
    if not data:
        return []
    return (data.get("pairs") or [])[:limit]


def dex_get_new_pairs(chain: str) -> list[dict]:
    """Search for recently created pairs on a chain."""
    data = _curl(f"{DEXSCREENER_BASE}/latest/dex/search?q={chain}")
    if not data:
        return []
    pairs = data.get("pairs") or []
    # sort by creation time desc, pick recent ones
    recent = sorted(
        [p for p in pairs if p.get("pairCreatedAt")],
        key=lambda p: p["pairCreatedAt"],
        reverse=True
    )
    return recent[:20]


# ── OnchainOS DEX (X Layer + EVM) ────────────────────────────────────────────

OKX_BASE = "https://web3.okx.com"

# Chain IDs supported by OnchainOS DEX (V6)
ONCHAINOS_CHAINS = {
    "xlayer": 196,
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "arbitrum": 42161,
    "base": 8453,
    "optimism": 10,
    "avalanche": 43114,
}


def _okx_headers(path: str, api_key: str, secret: str, passphrase: str) -> dict:
    ts = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    sig = base64.b64encode(
        hmac.new(secret.encode(), (ts + "GET" + path).encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": sig,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }


def okx_get_token_price(chain_index: int, token_address: str,
                         api_key: str, secret: str, passphrase: str) -> Optional[dict]:
    """Get token price via OKX DEX aggregator."""
    # Use USDC as quote token (0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE = native)
    path = f"/api/v6/dex/aggregator/quote?chainId={chain_index}&fromTokenAddress={token_address}&toTokenAddress=0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE&amount=1000000"
    headers = _okx_headers(path, api_key, secret, passphrase)
    data = _curl(OKX_BASE + path, headers)
    if data and data.get("code") == "0":
        return data.get("data", [{}])[0]
    return None


def okx_get_xlayer_tokens(api_key: str, secret: str, passphrase: str) -> list[dict]:
    """Get tokens available on X Layer via OKX DEX."""
    path = "/api/v6/dex/aggregator/all-tokens?chainIndex=196"
    headers = _okx_headers(path, api_key, secret, passphrase)
    data = _curl(OKX_BASE + path, headers)
    if data and data.get("code") == "0":
        return data.get("data", [])
    return []


def okx_get_supported_chains(api_key: str, secret: str, passphrase: str) -> list[dict]:
    path = "/api/v6/dex/aggregator/supported/chain"
    headers = _okx_headers(path, api_key, secret, passphrase)
    data = _curl(OKX_BASE + path, headers)
    if data and data.get("code") == "0":
        return data.get("data", [])
    return []


# ── Aggregated scan ───────────────────────────────────────────────────────────

def scan_all_signals(api_key: str, secret: str, passphrase: str) -> list[dict]:
    """
    Pull candidate tokens from all sources and enrich with pair data.
    Returns a list of token signal dicts ready for scoring.
    """
    candidates = {}  # key: (chain, address)

    print("  [1/4] Fetching DexScreener boosted tokens...")
    for t in dex_get_boosted_tokens():
        chain = t.get("chainId", "")
        addr = t.get("tokenAddress", "")
        if chain and addr:
            key = (chain, addr)
            candidates[key] = {"chain": chain, "address": addr, "source": "boost",
                                "description": t.get("description", "")}

    print("  [2/4] Fetching DexScreener new token profiles...")
    for t in dex_get_token_profiles():
        chain = t.get("chainId", "")
        addr = t.get("tokenAddress", "")
        if chain and addr:
            key = (chain, addr)
            if key not in candidates:
                candidates[key] = {"chain": chain, "address": addr, "source": "new_profile",
                                    "description": t.get("description", "")}

    print("  [3/4] Fetching X Layer tokens via OnchainOS...")
    xlayer_tokens = okx_get_xlayer_tokens(api_key, secret, passphrase)
    for t in xlayer_tokens[:20]:
        addr = t.get("tokenContractAddress", "")
        if addr and addr != "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE":
            key = ("xlayer", addr)
            candidates[key] = {
                "chain": "xlayer", "address": addr, "source": "xlayer_dex",
                "symbol": t.get("tokenSymbol", ""), "name": t.get("tokenName", ""),
                "description": ""
            }

    print(f"  [4/4] Enriching {len(candidates)} candidates with pair data...")
    enriched = []
    for (chain, addr), meta in list(candidates.items()):
        # For DexScreener chains, get pair data
        if chain in SUPPORTED_CHAINS:
            pairs = dex_get_pairs_for_token(chain, addr)
            if not pairs:
                continue
            # Pick the most liquid pair
            pair = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
            enriched.append({
                **meta,
                "symbol": pair.get("baseToken", {}).get("symbol", "?"),
                "name": pair.get("baseToken", {}).get("name", "?"),
                "price_usd": pair.get("priceUsd"),
                "volume_h24": (pair.get("volume") or {}).get("h24"),
                "volume_h6":  (pair.get("volume") or {}).get("h6"),
                "volume_h1":  (pair.get("volume") or {}).get("h1"),
                "liquidity_usd": (pair.get("liquidity") or {}).get("usd"),
                "price_change_m5":  (pair.get("priceChange") or {}).get("m5"),
                "price_change_h1":  (pair.get("priceChange") or {}).get("h1"),
                "price_change_h6":  (pair.get("priceChange") or {}).get("h6"),
                "price_change_h24": (pair.get("priceChange") or {}).get("h24"),
                "buys_h1":  (pair.get("txns") or {}).get("h1", {}).get("buys"),
                "sells_h1": (pair.get("txns") or {}).get("h1", {}).get("sells"),
                "buys_h24":  (pair.get("txns") or {}).get("h24", {}).get("buys"),
                "sells_h24": (pair.get("txns") or {}).get("h24", {}).get("sells"),
                "fdv": pair.get("fdv"),
                "pair_created_at": pair.get("pairCreatedAt"),
                "dex_id": pair.get("dexId"),
                "pair_url": pair.get("url"),
            })
        elif chain == "xlayer":
            enriched.append({
                **meta,
                "price_usd": None, "volume_h24": None, "volume_h6": None, "volume_h1": None,
                "liquidity_usd": None, "price_change_m5": None, "price_change_h1": None,
                "price_change_h6": None, "price_change_h24": None,
                "buys_h1": None, "sells_h1": None, "buys_h24": None, "sells_h24": None,
                "fdv": None, "pair_created_at": None, "dex_id": "okx_xlayer", "pair_url": None,
            })
        time.sleep(0.05)  # be polite to DexScreener

    return enriched
