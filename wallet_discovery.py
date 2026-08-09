#!/usr/bin/env python3
"""
Wallet discovery — auto-finds high-potential Solana wallets to copy-trade.

whales.py only tracks a hand-curated list (VERIFIED_WHALES). This module finds
NEW candidates automatically: whenever a token is already pumping hard in the
current scan, it looks up who bought it earliest (via Helius) and keeps a
running scorecard per wallet in paper_trades.json (state["wallet_scores"]).

A wallet gets "promoted" once it shows up as an early buyer on multiple big
movers. Once promoted, Signal Scout starts monitoring its future buys the same
way it monitors VERIFIED_WHALES, and a one-time Telegram alert goes out with
the address + explorer links so you can vet it and decide whether to copy it.
"""

import time, json, subprocess

SOL_MINT = "So11111111111111111111111111111111111111112"

# ── Tuning ───────────────────────────────────────────────────────────────────
PUMP_TRIGGER_PCT    = 50   # min 1h momentum on a token before we look up its early buyers
MAX_TOKENS_PER_SCAN = 5    # cap Helius calls per run
MAX_EARLY_BUYERS    = 6    # earliest unique wallets pulled per token
HIT_TTL_DAYS        = 14   # only hits from the last N days count toward promotion
PROMOTE_MIN_HITS    = 2    # early-buyer hits needed...
PROMOTE_MIN_TOKENS  = 2    # ...across at least this many different tokens
MAX_HITS_STORED     = 20
STALE_PRUNE_DAYS    = 30   # drop un-promoted wallets with no activity in this long


def _curl(url, key=""):
    full_url = f"{url}&api-key={key}" if key and "?" in url else (
               f"{url}?api-key={key}" if key else url)
    r = subprocess.run(["curl", "-s", "--max-time", "12", full_url],
                       capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8"))
    except Exception:
        return None


def _early_buyers(helius_key, mint, limit=MAX_EARLY_BUYERS):
    """Earliest unique wallets seen receiving `mint` in a SWAP, oldest first."""
    txns = _curl(
        f"https://api.helius.xyz/v0/addresses/{mint}/transactions"
        f"?limit=100&type=SWAP", helius_key
    ) or []
    txns = sorted(txns, key=lambda tx: tx.get("timestamp") or 0)

    buyers, seen = [], set()
    for txn in txns:
        for transfer in txn.get("tokenTransfers", []):
            if transfer.get("mint") != mint:
                continue
            buyer = transfer.get("toUserAccount", "")
            if not buyer or buyer in seen or buyer == mint:
                continue
            seen.add(buyer)
            buyers.append(buyer)
            if len(buyers) >= limit:
                return buyers
    return buyers


# ── Discovery ────────────────────────────────────────────────────────────────

def discover_from_pumpers(helius_key, tokens, state, verified_addrs=None):
    """
    Look at the hardest-pumping Solana tokens from this scan, find their
    earliest buyers, and update each wallet's scorecard in state.
    Returns [(address, info), ...] for wallets newly promoted this run.
    """
    if not helius_key:
        return []

    verified_addrs = verified_addrs or set()
    scores = state.setdefault("wallet_scores", {})

    candidates = [
        t for t in tokens
        if t.get("chain") == "solana"
        and t.get("address")
        and (t.get("price_change_h1") or 0) >= PUMP_TRIGGER_PCT
    ]
    candidates.sort(key=lambda t: t.get("price_change_h1") or 0, reverse=True)
    candidates = candidates[:MAX_TOKENS_PER_SCAN]

    now      = time.time()
    cutoff   = now - HIT_TTL_DAYS * 86400
    newly_promoted = []

    for t in candidates:
        mint = t["address"]
        try:
            buyers = _early_buyers(helius_key, mint)
        except Exception as ex:
            print(f"  wallet_discovery error {mint[:8]}: {ex}")
            continue

        for addr in buyers:
            if addr in verified_addrs:
                continue   # already a known whale — no need to rediscover

            entry = scores.setdefault(addr, {
                "hits": [], "first_seen": now, "last_seen": now,
                "promoted": False, "promoted_at": None,
            })
            if any(h["mint"] == mint for h in entry["hits"]):
                continue   # already credited for this token

            entry["hits"].append({
                "mint":     mint,
                "symbol":   t.get("symbol", "?"),
                "pct_gain": round(t.get("price_change_h1") or 0, 1),
                "ts":       now,
            })
            entry["hits"]      = entry["hits"][-MAX_HITS_STORED:]
            entry["last_seen"] = now

            recent = [h for h in entry["hits"] if h["ts"] >= cutoff]
            unique_tokens = {h["mint"] for h in recent}

            if (not entry["promoted"]
                    and len(recent) >= PROMOTE_MIN_HITS
                    and len(unique_tokens) >= PROMOTE_MIN_TOKENS):
                entry["promoted"]    = True
                entry["promoted_at"] = now
                newly_promoted.append((addr, entry))

        time.sleep(0.15)

    # ── Prune stale, never-promoted wallets so state doesn't grow forever ──────
    stale_cutoff = now - STALE_PRUNE_DAYS * 86400
    for addr in list(scores.keys()):
        info = scores[addr]
        if not info.get("promoted") and (info.get("last_seen") or 0) < stale_cutoff:
            del scores[addr]

    return newly_promoted


# ── Monitor promoted wallets for new buys ───────────────────────────────────

def get_discovered_wallet_buys(helius_key, promoted, lookback_minutes=15):
    """
    Check all promoted (auto-discovered) wallets for NEW token buys in the
    last N minutes. Mirrors whales.get_whale_buys() but for this dynamic list.
    Returns enriched-ready token signal dicts.
    """
    if not helius_key or not promoted:
        return []

    signals   = []
    seen      = set()
    cutoff_ts = time.time() - lookback_minutes * 60

    for addr, info in promoted.items():
        try:
            txns = _curl(
                f"https://api.helius.xyz/v0/addresses/{addr}/transactions"
                f"?limit=20&type=SWAP", helius_key
            ) or []

            for txn in txns:
                if (txn.get("timestamp") or 0) < cutoff_ts:
                    continue
                for transfer in txn.get("tokenTransfers", []):
                    if transfer.get("toUserAccount") != addr:
                        continue
                    mint = transfer.get("mint", "")
                    if not mint or mint == SOL_MINT or mint in seen:
                        continue

                    seen.add(mint)
                    hit_tokens = ", ".join(sorted({h["symbol"] for h in info.get("hits", [])}))
                    signals.append({
                        "chain":                    "solana",
                        "address":                  mint,
                        "source":                   "discovered_wallet",
                        "icon":                     "",
                        "description":              "",
                        "pair_age_minutes":         None,
                        "liquidity_usd":            0,
                        "volume_h1":                0,
                        "volume_h24":               0,
                        "price_change_h1":          None,
                        "price_change_h24":         None,
                        "buys_h1":                  0,
                        "sells_h1":                 0,
                        "price_usd":                None,
                        "fdv":                      None,
                        "pair_url":                 f"https://dexscreener.com/solana/{mint}",
                        "dex_id":                   "raydium",
                        "pump_progress":            None,
                        "discovered_wallet_addr":   addr,
                        "discovered_wallet_tokens": hit_tokens,
                        "discovered_wallet_hits":   len(info.get("hits", [])),
                    })
            time.sleep(0.15)

        except Exception as ex:
            print(f"  Discovered-wallet error {addr[:8]}: {ex}")

    return signals


# ── Telegram formatting ──────────────────────────────────────────────────────

def format_discovery_alert(addr, info):
    tokens = info.get("hits", [])
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "  🕵️ NEW WALLET DISCOVERED",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Early buyer on <b>{len(tokens)}</b> tokens that pumped hard.",
        "Signal Scout will now watch this wallet and alert you when it buys.",
        "",
        "─── Track record ───────────",
    ]
    for h in tokens[-5:]:
        lines.append(f"  • <b>{h['symbol']}</b>  +{h['pct_gain']:.0f}%  (1h at time of scan)")

    lines += [
        "",
        f"📋 <code>{addr}</code>",
        "",
        f"🔎 <a href='https://gmgn.ai/sol/address/{addr}'>GMGN</a>"
        f" · <a href='https://app.cielo.finance/profile/{addr}'>Cielo</a>"
        f" · <a href='https://solscan.io/account/{addr}'>Solscan</a>",
        "",
        "Vet it yourself before copying — this is a heuristic, not a guarantee.",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def wallet_discovery_summary(state):
    """Formatted list of currently-promoted auto-discovered wallets."""
    scores   = state.get("wallet_scores", {})
    promoted = {a: i for a, i in scores.items() if i.get("promoted")}

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "  🕵️ DISCOVERED WALLETS",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    if not promoted:
        watching = len(scores)
        lines.append(f"No wallets promoted yet. Currently watching {watching} candidate(s).")
    else:
        for addr, info in sorted(promoted.items(),
                                  key=lambda kv: len(kv[1].get("hits", [])), reverse=True):
            hits = info.get("hits", [])
            syms = ", ".join(sorted({h["symbol"] for h in hits}))
            lines.append(
                f"  <code>{addr[:4]}…{addr[-4:]}</code>  |  {len(hits)} hits  |  {syms[:40]}"
            )
    lines += [
        "",
        "📌 Promoted wallets are auto-monitored for new buys.",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)
