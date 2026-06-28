"""
twitter_search.py — X (Twitter) alpha caller scanner for Signal Scout.

Watches a list of trusted X accounts (alpha callers) for recent posts that
mention a contract address, extracts the CA, and returns token seeds for
enrichment as source='x_alpha'. This is what catches narrative/influencer
driven tokens (e.g. a coin trending because of a specific X personality)
that never show up in DexScreener boost/new-pair feeds.

Requires the X API v2 "recent search" endpoint, which needs a paid tier
(the free tier does not include search) — https://developer.x.com/en/portal/products

Setup (one-time):
  1. Create a Project + App on the X Developer Portal, generate a Bearer Token.
  2. Add to GitHub Secrets:
       TWITTER_BEARER_TOKEN
       X_ALPHA_ACCOUNTS   (comma-separated handles, no @, e.g. "ansem,inversebrah")

Env vars:
  TWITTER_BEARER_TOKEN    X API v2 bearer token (required)
  X_ALPHA_ACCOUNTS        comma-sep handles to watch (required)
  X_ALPHA_LOOKBACK_MIN    how far back to scan per run (default: 20)
  X_ALPHA_MIN_LIKES       min like count for a post to count as a signal (default: 0)
"""

import os, re, json, subprocess, time
from datetime import datetime, timezone, timedelta

SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"

# Solana: base58, 32-44 chars (excludes obvious non-CA tokens like short words)
SOLANA_RE = re.compile(r'\b([1-9A-HJ-NP-Za-km-z]{32,44})\b')
# EVM: 0x + 40 hex chars
EVM_RE    = re.compile(r'\b(0x[0-9a-fA-F]{40})\b')

# Addresses to always ignore (pump.fun program, system program, etc.)
BLOCKLIST = {
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
}


def _search_recent(query, start_time, bearer):
    cmd = [
        "curl", "-s", "-m", "15", "-G",
        "-H", f"Authorization: Bearer {bearer}",
        "--data-urlencode", f"query={query}",
        "--data-urlencode", f"start_time={start_time}",
        "--data-urlencode", "max_results=100",
        "--data-urlencode", "tweet.fields=public_metrics,author_id,created_at",
        "--data-urlencode", "expansions=author_id",
        "--data-urlencode", "user.fields=username",
        SEARCH_URL,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return json.loads(r.stdout) if r.stdout.strip() else None
    except Exception:
        return None


def _record(found, addr, chain, likes, rts, handle, text):
    rec = found.get(addr)
    if rec is not None and rec["likes"] >= likes:
        return  # keep the highest-engagement mention as the attributed caller
    found[addr] = {
        "chain":   chain,
        "caller":  f"@{handle}" if handle else "@unknown",
        "likes":   likes,
        "rts":     rts,
        "snippet": text[:120].replace("\n", " "),
    }


def fetch_x_tokens():
    """
    Search recent posts from configured trusted X accounts, extract contract
    addresses, return seed dicts for enrichment.
    """
    bearer       = os.getenv("TWITTER_BEARER_TOKEN", "")
    accounts_raw = os.getenv("X_ALPHA_ACCOUNTS", "")
    if not bearer or not accounts_raw:
        print("  x_alpha: missing TWITTER_BEARER_TOKEN or X_ALPHA_ACCOUNTS — skipping")
        return []

    accounts     = [a.strip().lstrip("@") for a in accounts_raw.split(",") if a.strip()]
    lookback_min = int(os.getenv("X_ALPHA_LOOKBACK_MIN", "20"))
    min_likes    = int(os.getenv("X_ALPHA_MIN_LIKES", "0"))
    start_time   = (datetime.now(timezone.utc) - timedelta(minutes=lookback_min)).strftime("%Y-%m-%dT%H:%M:%SZ")

    found: dict = {}  # addr -> {chain, caller, likes, rts, snippet}

    for i in range(0, len(accounts), 25):  # X API query length limits — chunk accounts
        chunk = accounts[i:i + 25]
        query = "(" + " OR ".join(f"from:{a}" for a in chunk) + ") -is:retweet"
        try:
            data = _search_recent(query, start_time, bearer)
        except Exception as e:
            print(f"  x_alpha: search error — {e}")
            continue
        if not data:
            continue
        if data.get("errors") or data.get("title") == "UsageCapExceeded":
            print(f"  x_alpha: API error — {data.get('errors') or data.get('title')}")
            continue

        tweets = data.get("data") or []
        users  = {u.get("id"): u.get("username", "") for u in (data.get("includes") or {}).get("users", [])}

        for tw in tweets:
            metrics = tw.get("public_metrics") or {}
            likes   = metrics.get("like_count", 0)
            rts     = metrics.get("retweet_count", 0)
            if likes < min_likes:
                continue
            text   = tw.get("text", "")
            handle = users.get(tw.get("author_id", ""), "")

            for addr in SOLANA_RE.findall(text):
                if addr in BLOCKLIST or addr.startswith("0x"):
                    continue
                _record(found, addr, "solana", likes, rts, handle, text)

            for addr in EVM_RE.findall(text):
                if addr in BLOCKLIST:
                    continue
                _record(found, addr, "evm", likes, rts, handle, text)

        time.sleep(0.3)

    tokens = []
    for addr, rec in found.items():
        tokens.append({
            "chain":     rec["chain"],
            "address":   addr,
            "source":    "x_alpha",
            "x_caller":  rec["caller"],
            "x_likes":   rec["likes"],
            "x_rts":     rec["rts"],
            "x_snippet": rec["snippet"],
        })

    tokens.sort(key=lambda x: x["x_likes"], reverse=True)
    print(f"  X alpha candidates: {len(tokens)} across {len(accounts)} accounts")
    return tokens
