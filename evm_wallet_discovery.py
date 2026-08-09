#!/usr/bin/env python3
"""
EVM wallet discovery — auto-finds Ethereum/BSC/Base/Arbitrum wallets to
copy-trade, using Etherscan's multichain V2 API (one free key covers all
four chains — the same ones already used for honeypot checks).

For each token that's already pumping hard in the current scan, this module
walks that token's FULL on-chain transfer history from the very first
transfers (not just recent activity) to find its earliest holders, confirms
they didn't instantly flip it (bots/snipers get filtered out), and credits
them in a scorecard (state["evm_wallet_scores"]). A wallet gets "promoted"
once it shows this pattern on multiple tokens — Signal Scout then watches
its future token buys and alerts you.

It also does simple funding-source clustering: a wallet's very first
incoming ETH/BNB/etc transfer usually comes from whoever funded it. Two
wallets funded from the same address are treated as "associated" — if one
gets promoted, its associate is watched too, even before it earns its own
track record.

Requires ETHERSCAN_API_KEY (free — https://etherscan.io/apis). Degrades to
a no-op everywhere if the key isn't set, same as every other optional
module in this bot.
"""

import time, json, subprocess

CHAIN_IDS = {"ethereum": 1, "bsc": 56, "base": 8453, "arbitrum": 42161}
ZERO_ADDR = "0x0000000000000000000000000000000000000000"

# ── Tuning ───────────────────────────────────────────────────────────────────
PUMP_TRIGGER_PCT    = 50    # min 1h momentum before we look up a token's early holders
MAX_TOKENS_PER_SCAN = 4     # cap Etherscan calls per run, across all EVM chains combined
MAX_EARLY_BUYERS    = 5     # earliest unique wallets pulled per token
MIN_HOLD_MINUTES    = 20    # must not have sold the token within this long of first buy
HIT_TTL_DAYS        = 14
PROMOTE_MIN_HITS    = 2
PROMOTE_MIN_TOKENS  = 2
MAX_HITS_STORED     = 20
STALE_PRUNE_DAYS    = 30


def _curl(chain, module, action, params, api_key):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = (f"https://api.etherscan.io/v2/api?chainid={CHAIN_IDS[chain]}"
           f"&module={module}&action={action}&{qs}&apikey={api_key}")
    r = subprocess.run(["curl", "-s", "--max-time", "12", url], capture_output=True)
    try:
        data = json.loads(r.stdout.decode("utf-8"))
    except Exception:
        return None
    time.sleep(0.25)   # stay well under free-tier rate limits across 4 chains
    return data


def _token_transfers(chain, api_key, contract=None, address=None, sort="asc", limit=100):
    params = {"sort": sort, "page": 1, "offset": limit}
    if contract:
        params["contractaddress"] = contract
    if address:
        params["address"] = address
    d = _curl(chain, "account", "tokentx", params, api_key) or {}
    if d.get("status") != "1":
        return []
    return d.get("result") or []


def _is_contract(chain, addr, api_key):
    d = _curl(chain, "proxy", "eth_getCode", {"address": addr, "tag": "latest"}, api_key) or {}
    code = d.get("result", "0x")
    return bool(code) and code != "0x"


def _first_funder(chain, addr, api_key):
    d = _curl(chain, "account", "txlist",
              {"address": addr, "sort": "asc", "page": 1, "offset": 1}, api_key) or {}
    if d.get("status") != "1":
        return None
    result = d.get("result") or []
    return result[0].get("from") if result else None


def _held_through_pump(chain, wallet, contract, api_key, min_hold_minutes=MIN_HOLD_MINUTES):
    """True if `wallet` didn't sell `contract` within min_hold_minutes of first receiving it."""
    txs = _token_transfers(chain, api_key, contract=contract, address=wallet, sort="asc", limit=30)
    if not txs:
        return False
    first_buy_ts = None
    for tx in txs:
        ts = int(tx.get("timeStamp") or 0)
        to_addr, from_addr = tx.get("to", "").lower(), tx.get("from", "").lower()
        if to_addr == wallet.lower() and first_buy_ts is None:
            first_buy_ts = ts
            continue
        if from_addr == wallet.lower() and first_buy_ts is not None:
            if (ts - first_buy_ts) < min_hold_minutes * 60:
                return False   # sold too fast — likely a bot/sniper, not a smart holder
    return first_buy_ts is not None


def _early_buyers(chain, contract, api_key, limit=MAX_EARLY_BUYERS):
    txs = _token_transfers(chain, api_key, contract=contract, sort="asc", limit=100)
    buyers, seen = [], set()
    for tx in txs:
        to_addr = tx.get("to", "")
        if not to_addr or to_addr.lower() in (contract.lower(), ZERO_ADDR):
            continue
        if to_addr in seen:
            continue
        seen.add(to_addr)
        if _is_contract(chain, to_addr, api_key):
            continue   # skip routers / pair contracts / other infra
        if not _held_through_pump(chain, to_addr, contract, api_key):
            continue
        buyers.append(to_addr)
        if len(buyers) >= limit:
            break
    return buyers


# ── Discovery ────────────────────────────────────────────────────────────────

def discover_evm_from_pumpers(api_key, tokens, state):
    """
    Look at the hardest-pumping EVM tokens from this scan, walk their full
    transfer history to find earliest holders who didn't instantly flip,
    and update each wallet's scorecard. Returns [(chain, addr, info), ...]
    for wallets newly promoted this run (directly or via association).
    """
    if not api_key:
        return []

    scores = state.setdefault("evm_wallet_scores", {})   # key: f"{chain}:{addr}"

    candidates = [
        t for t in tokens
        if t.get("chain") in CHAIN_IDS
        and t.get("address")
        and (t.get("price_change_h1") or 0) >= PUMP_TRIGGER_PCT
    ]
    candidates.sort(key=lambda t: t.get("price_change_h1") or 0, reverse=True)
    candidates = candidates[:MAX_TOKENS_PER_SCAN]

    now    = time.time()
    cutoff = now - HIT_TTL_DAYS * 86400
    newly_promoted = []

    for t in candidates:
        chain    = t["chain"]
        contract = t["address"]
        try:
            buyers = _early_buyers(chain, contract, api_key)
        except Exception as ex:
            print(f"  evm_wallet_discovery error {contract[:10]}: {ex}")
            continue

        for addr in buyers:
            key = f"{chain}:{addr}"
            entry = scores.setdefault(key, {
                "chain": chain, "address": addr, "hits": [],
                "first_seen": now, "last_seen": now,
                "promoted": False, "promoted_at": None,
                "promoted_via": None, "funder": None,
            })
            if any(h["contract"] == contract for h in entry["hits"]):
                continue

            entry["hits"].append({
                "contract": contract,
                "symbol":   t.get("symbol", "?"),
                "pct_gain": round(t.get("price_change_h1") or 0, 1),
                "ts":       now,
            })
            entry["hits"]      = entry["hits"][-MAX_HITS_STORED:]
            entry["last_seen"] = now

            if entry["funder"] is None:
                try:
                    entry["funder"] = _first_funder(chain, addr, api_key)
                except Exception:
                    pass

            recent = [h for h in entry["hits"] if h["ts"] >= cutoff]
            unique_tokens = {h["contract"] for h in recent}
            if (not entry["promoted"]
                    and len(recent) >= PROMOTE_MIN_HITS
                    and len(unique_tokens) >= PROMOTE_MIN_TOKENS):
                entry["promoted"]     = True
                entry["promoted_at"]  = now
                entry["promoted_via"] = "track_record"
                newly_promoted.append((chain, addr, entry))

    # ── Funding-cluster association — promote siblings of a promoted wallet ────
    funders = {}
    for key, info in scores.items():
        if info.get("funder"):
            funders.setdefault((info["chain"], info["funder"]), []).append(key)

    for key, info in scores.items():
        if info.get("promoted") or not info.get("funder"):
            continue
        siblings = funders.get((info["chain"], info["funder"]), [])
        if any(scores[sib].get("promoted") for sib in siblings if sib != key and sib in scores):
            info["promoted"]     = True
            info["promoted_at"]  = now
            info["promoted_via"] = "associated_wallet"
            newly_promoted.append((info["chain"], info["address"], info))

    # ── Prune stale, never-promoted wallets ────────────────────────────────────
    stale_cutoff = now - STALE_PRUNE_DAYS * 86400
    for key in list(scores.keys()):
        info = scores[key]
        if not info.get("promoted") and (info.get("last_seen") or 0) < stale_cutoff:
            del scores[key]

    return newly_promoted


# ── Monitor promoted wallets for new buys ───────────────────────────────────

def get_evm_wallet_buys(api_key, state, lookback_minutes=15):
    """Check all promoted EVM wallets for NEW token buys in the last N minutes."""
    scores = state.get("evm_wallet_scores", {})
    promoted = {k: v for k, v in scores.items() if v.get("promoted")}
    if not api_key or not promoted:
        return []

    signals   = []
    seen      = set()
    cutoff_ts = time.time() - lookback_minutes * 60

    for key, info in promoted.items():
        chain, addr = info["chain"], info["address"]
        try:
            txs = _token_transfers(chain, api_key, address=addr, sort="desc", limit=20)
            for tx in txs:
                if int(tx.get("timeStamp") or 0) < cutoff_ts:
                    continue
                if tx.get("to", "").lower() != addr.lower():
                    continue
                contract = tx.get("contractAddress", "")
                dedupe_key = f"{chain}:{contract}"
                if not contract or dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                hit_tokens = ", ".join(sorted({h["symbol"] for h in info.get("hits", [])}))
                signals.append({
                    "chain":                    chain,
                    "address":                  contract,
                    "source":                   "discovered_wallet_evm",
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
                    "pair_url":                 f"https://dexscreener.com/{chain}/{contract}",
                    "dex_id":                   "",
                    "pump_progress":            None,
                    "discovered_wallet_addr":   addr,
                    "discovered_wallet_tokens": hit_tokens,
                    "discovered_wallet_hits":   len(info.get("hits", [])),
                    "discovered_wallet_via":    info.get("promoted_via", "track_record"),
                })
        except Exception as ex:
            print(f"  EVM discovered-wallet error {addr[:10]}: {ex}")

    return signals


# ── Telegram formatting ──────────────────────────────────────────────────────

def format_evm_discovery_alert(chain, addr, info):
    tokens = info.get("hits", [])
    via = info.get("promoted_via", "track_record")
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"  🕵️ NEW {chain.upper()} WALLET DISCOVERED",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    if via == "associated_wallet":
        lines.append("Funded by the same wallet as an already-proven wallet.")
    else:
        lines.append(f"Early holder on <b>{len(tokens)}</b> tokens that pumped hard — didn't instantly flip.")
    lines.append("Signal Scout will now watch this wallet and alert you when it buys.")
    lines.append("")
    lines.append("─── Track record ───────────")
    for h in tokens[-5:]:
        lines.append(f"  • <b>{h['symbol']}</b>  +{h['pct_gain']:.0f}%  (1h at time of scan)")
    if not tokens:
        lines.append("  — (promoted via funding association, no direct hits yet)")

    explorer = {"ethereum": "etherscan.io", "bsc": "bscscan.com",
                "base": "basescan.org", "arbitrum": "arbiscan.io"}.get(chain, "etherscan.io")
    lines += [
        "",
        f"📋 <code>{addr}</code>",
        "",
        f"🔎 <a href='https://gmgn.ai/{chain}/address/{addr}'>GMGN</a>"
        f" · <a href='https://{explorer}/address/{addr}'>Explorer</a>",
        "",
        "Vet it yourself before copying — this is a heuristic, not a guarantee.",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def evm_wallet_discovery_summary(state):
    scores   = state.get("evm_wallet_scores", {})
    promoted = {k: v for k, v in scores.items() if v.get("promoted")}

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "  🕵️ DISCOVERED EVM WALLETS",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    if not promoted:
        lines.append(f"No wallets promoted yet. Currently watching {len(scores)} candidate(s).")
    else:
        for key, info in sorted(promoted.items(),
                                 key=lambda kv: len(kv[1].get("hits", [])), reverse=True):
            addr = info["address"]
            syms = ", ".join(sorted({h["symbol"] for h in info.get("hits", [])})) or "(via association)"
            lines.append(
                f"  [{info['chain'][:3].upper()}] <code>{addr[:4]}…{addr[-4:]}</code>  |  "
                f"{len(info.get('hits', []))} hits  |  {syms[:35]}"
            )
    lines += [
        "",
        "📌 Promoted wallets are auto-monitored for new buys.",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)
