#!/usr/bin/env python3
"""
Signal Scout — Multi-Chain Meme Token Signal Analyzer
OKX Build X Hackathon | X Layer Arena

Data sources: DexScreener (Solana/ETH/BSC/Base/Arbitrum) + OKX OnchainOS DEX (X Layer)
Scoring: Claude claude-sonnet-4-6 AI
Platform: Moltbook m/buildx
"""

import os
import sys
import json
import argparse
import datetime
from pathlib import Path

# Load .env
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from fetcher import scan_all_signals
from scorer import score_tokens
from reporter import format_report, post_to_moltbook, solve_verification


def load_credentials() -> dict:
    return {
        "okx_api_key":    os.environ["OKX_API_KEY"],
        "okx_secret":     os.environ["OKX_SECRET_KEY"],
        "okx_passphrase": os.environ["OKX_PASSPHRASE"],
        "moltbook_key":   os.environ["MOLTBOOK_API_KEY"],
    }


def run_scan(creds: dict, top_n: int = 10, post: bool = False, save: bool = True) -> list[dict]:
    print("\n═══════════════════════════════════════════")
    print("  Signal Scout — Multi-Chain Token Scanner")
    print(f"  {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("═══════════════════════════════════════════\n")

    print("► Phase 1: Scanning token signals across chains...")
    tokens = scan_all_signals(
        api_key=creds["okx_api_key"],
        secret=creds["okx_secret"],
        passphrase=creds["okx_passphrase"],
    )
    print(f"  ✓ {len(tokens)} candidate tokens found\n")

    print("► Phase 2: Scoring with Claude AI...")
    scored = score_tokens(tokens, top_n=min(len(tokens), 30))
    print(f"  ✓ Scored {len(scored)} tokens\n")

    print("► Phase 3: Generating report...")
    report = format_report(scored, top_n=top_n)
    print(report)

    if save:
        out_path = Path(__file__).parent / f"report_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M')}.md"
        out_path.write_text(report, encoding="utf-8")
        print(f"\n  ✓ Report saved to {out_path.name}")

    if post:
        print("\n► Phase 4: Posting to Moltbook m/buildx...")
        resp = post_to_moltbook(report, creds["moltbook_key"])
        print(f"  Response: {json.dumps(resp, indent=2)[:400]}")

        # Handle verification challenge
        verification = resp.get("post", {}).get("verification") or {}
        if verification.get("verification_code"):
            code = verification["verification_code"]
            challenge = verification.get("challenge_text", "")
            print(f"\n  Solving verification challenge: {challenge[:80]}...")
            v_resp = solve_verification(code, challenge, creds["moltbook_key"])
            print(f"  Verification result: {v_resp}")

    return scored


def main():
    parser = argparse.ArgumentParser(description="Signal Scout — meme token signal analyzer")
    parser.add_argument("--top", type=int, default=10, help="Number of top tokens to show (default: 10)")
    parser.add_argument("--post", action="store_true", help="Post report to Moltbook m/buildx")
    parser.add_argument("--no-save", action="store_true", help="Don't save report to file")
    parser.add_argument("--json", action="store_true", help="Also output raw JSON results")
    args = parser.parse_args()

    try:
        creds = load_credentials()
    except KeyError as e:
        print(f"Missing credential: {e}. Check your .env file.")
        sys.exit(1)

    scored = run_scan(creds, top_n=args.top, post=args.post, save=not args.no_save)

    if args.json:
        out = Path(__file__).parent / "results.json"
        out.write_text(json.dumps(scored, indent=2, default=str), encoding="utf-8")
        print(f"\nJSON results saved to {out.name}")


if __name__ == "__main__":
    main()
