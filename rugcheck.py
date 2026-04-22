"""
Rug and honeypot detection layer.
Sources: honeypot.is (ETH/BSC/Base),RugCheck.xyz (Solana)
"""

import json
import subprocess


def _curl(url: str) -> dict | None:
    r = subprocess.run(["curl", "-s", "--max-time", "8", url], capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8"))
    except Exception:
        return None


def check_honeypot_evm(address: str, chain_id: str) -> dict:
    """
    Check EVM token via honeypot.is.
    chain_id: '1'=ETH, '56'=BSC, '8453'=Base, '196'=XLayer
    """
    data = _curl(f"https://api.honeypot.is/v2/IsHoneypot?address={address}&chainID={chain_id}")
    if not data:
        return {"safe": None, "reason": "check unavailable"}

    is_honeypot = data.get("honeypotResult", {}).get("isHoneypot", False)
    simulation = data.get("simulationResult", {})
    token_info = data.get("token", {})

    buy_tax = simulation.get("buyTax", 0)
    sell_tax = simulation.get("sellTax", 0)

    issues = []
    if is_honeypot:
        issues.append("HONEYPOT - cannot sell")
    if sell_tax and sell_tax > 10:
        issues.append(f"high sell tax {sell_tax:.0f}%")
    if buy_tax and buy_tax > 10:
        issues.append(f"high buy tax {buy_tax:.0f}%")

    flags = data.get("flags", [])
    for f in flags:
        issues.append(f.get("flag", ""))

    return {
        "safe": not is_honeypot and (sell_tax or 0) <= 10,
        "is_honeypot": is_honeypot,
        "buy_tax": buy_tax,
        "sell_tax": sell_tax,
        "issues": issues,
        "holder_count": token_info.get("totalHolders"),
    }


def check_rugcheck_solana(address: str) -> dict:
    """Check Solana token via rugcheck.xyz."""
    data = _curl(f"https://api.rugcheck.xyz/v1/tokens/{address}/report/summary")
    if not data:
        return {"safe": None, "reason": "check unavailable"}

    score = data.get("score", 0)        # 0=good, higher=riskier
    risks = data.get("risks", [])
    risk_names = [r.get("name", "") for r in risks]

    is_safe = score < 500 and not any(
        r in ["Freeze Authority still enabled", "Mint Authority still enabled",
              "Low Liquidity", "Single holder ownership"]
        for r in risk_names
    )

    return {
        "safe": is_safe,
        "score": score,
        "risks": risk_names[:5],
        "issues": [r for r in risk_names if r],
    }


CHAIN_TO_ID = {
    "ethereum": "1",
    "bsc": "56",
    "base": "8453",
    "arbitrum": "42161",
    "xlayer": "196",
}


def check_token(token: dict) -> dict:
    """Run rug/honeypot check on a token. Returns enriched token dict."""
    chain = token.get("chain", "")
    address = token.get("address", "")

    if chain == "solana":
        result = check_rugcheck_solana(address)
    elif chain in CHAIN_TO_ID:
        result = check_honeypot_evm(address, CHAIN_TO_ID[chain])
    else:
        result = {"safe": None, "reason": "unsupported chain"}

    return {**token, "rug_check": result}


def is_safe(token: dict) -> bool:
    """Returns True if token passed rug check or check was unavailable."""
    rc = token.get("rug_check", {})
    safe = rc.get("safe")
    return safe is not False   # None (unavailable) = pass through


def rug_summary(token: dict) -> str:
    """One-line rug check summary for display."""
    rc = token.get("rug_check", {})
    if rc.get("safe") is None:
        return "⚪ Rug check N/A"
    if rc.get("is_honeypot"):
        return "🚨 HONEYPOT"
    issues = rc.get("issues", [])
    if issues:
        return f"⚠️ {', '.join(issues[:2])}"
    score = rc.get("score")
    sell_tax = rc.get("sell_tax")
    if sell_tax is not None:
        return f"✅ Safe (sell tax {sell_tax:.0f}%)"
    if score is not None:
        return f"✅ Safe (score {score})"
    return "✅ Safe"
