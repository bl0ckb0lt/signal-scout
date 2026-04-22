# Signal Scout 🔍

**Multi-Chain Meme Token Signal Analyzer**
OKX Build X Hackathon — X Layer Arena Track

---

## What It Does

Signal Scout scans tokens across **Solana, Ethereum, BSC, Base, Arbitrum, and X Layer** in real time, scores them using Claude AI across 5 signal dimensions, and ranks them by profit potential.

It answers one question: **which tokens have the strongest on-chain signals right now?**

---

## Signal Dimensions (scored 0–20 each)

| Dimension | What it measures |
|-----------|-----------------|
| **Momentum** | Price change consistency across 5m, 1h, 6h, 24h |
| **Volume Signal** | Volume relative to liquidity — how active is the market |
| **Market Structure** | Buy/sell ratio, liquidity depth, FDV reasonableness |
| **Token Quality** | Age, source (boosted = active marketing), chain ecosystem |
| **Risk/Reward** | Overall risk-adjusted opportunity score |

---

## Data Sources

| Source | Chains | Data |
|--------|--------|------|
| DexScreener API | Solana, ETH, BSC, Base, Arbitrum | Price, volume, liquidity, txns, price change |
| OKX OnchainOS DEX API | X Layer (chainId 196) + all EVM | Token list, on-chain data |
| Claude AI | — | Signal scoring and reasoning |

---

## Architecture

```
main.py          ← entry point, orchestrates the scan
fetcher.py       ← pulls data from DexScreener + OKX OnchainOS
scorer.py        ← Claude AI scoring engine (5 dimensions)
reporter.py      ← formats report + posts to Moltbook m/buildx
.env             ← credentials (git-ignored)
```

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/bl0ckb0lt/signal-scout
cd signal-scout

# 2. Add credentials to .env
cp .env.example .env
# fill in OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE, MOLTBOOK_API_KEY, ANTHROPIC_API_KEY

# 3. Run a scan
python main.py --top 10

# 4. Run and post to Moltbook
python main.py --top 10 --post
```

---

## Integration

- **OKX OnchainOS DEX API** — X Layer token list, chain data, DEX aggregator
- **Moltbook** — posts reports to [m/buildx](https://www.moltbook.com/m/buildx)
- **X Layer** — chainId 196, supported via OKX DEX aggregator

---

## Sample Output

```
MTGA   (Solana)  Score: 75  WATCH  — +56% 1h, $8.8M vol, BSR 1.56, 9h old
FOF    (Solana)  Score: 63  WATCH  — BSR 3.43, stable climb, 7 days old
MAGA   (Solana)  Score: 64  WATCH  — $466K liq, 61 days old, lowest rug risk
SPIKE  (Solana)  Score: 63  WATCH  — $350K liq, BSR 1.75, 2-week survivor
```

---

## Hackathon

- **Event:** OKX Build X Hackathon
- **Track:** X Layer Arena
- **Agent:** [claudecodeagent-buildx](https://www.moltbook.com/u/claudecodeagent-buildx) on Moltbook
- **Submission:** [m/buildx](https://www.moltbook.com/m/buildx)

---

*Not financial advice. Always DYOR before trading.*
