# Signal Scout — Claude Working Reference

> One-stop context file. Paste the contents of this file at the start of any new chat
> instead of re-reading source files. Keep it up to date after every major change.

---

## Project Identity

| Field | Value |
|-------|-------|
| Name | Signal Scout v5 |
| Purpose | Multi-chain meme-token signal bot — find tokens before they pump |
| Stack | Python 3, GitHub Actions (cron), no server, no DB |
| State persistence | `paper_trades.json` auto-committed to git after every scan |
| Repo | github.com/bl0ckb0lt/signal-scout |
| Hackathon | OKX Build X — X Layer Arena track |

---

## File Map

```
scan_and_alert.py   ← MAIN FILE. Fetch → filter → score → rug-check → alert → paper-trade
check_positions.py  ← runs every 5 min, calls check_exits() from scan_and_alert
commands.py         ← stateless Telegram command handler (cron-triggered)
bot_commands.py     ← alternative command interface
fetcher.py          ← token data fetching helpers (older module, partially superseded)
scorer.py           ← Claude AI scoring (older module, scoring now inline in scan_and_alert)
reporter.py         ← format_report() + post_to_moltbook()
telegram_bot.py     ← format_alert(), format_scan_summary() (older helpers)
trader.py           ← Jupiter real-trade execution (paper/semi/auto modes)
whales.py           ← VERIFIED_WHALES dict, get_whale_buys(), get_whale_exits(), whale_summary()
rugcheck.py         ← rug_check() helpers (also inlined in scan_and_alert)
sheets_logger.py    ← [NEW] Google Sheets trade log (sheets_log_open, sheets_log_close)
twitter_alerts.py   ← [NEW] Twitter/X signal posts (post_tweet, format_tweet)  [TO BE CREATED]
main.py             ← CLI entry point (python main.py --top 10 --post)
paper_trades.json   ← live state: open[], closed[], cooldown{}, last_update_id, paused
.env.example        ← env var template
requirements.txt    ← base58>=2.1.0, PyNaCl>=1.5.0 (+ gspread, google-auth, tweepy pending)
```

---

## Core Data Flow (scan_and_alert.py → main())

```
1. load_state()                   — reads paper_trades.json
2. poll_commands()                — processes Telegram commands (/pause /resume /status /trades /whales)
3. check_exits()                  — trailing stop / fixed SL / hard TP for all open positions
4. get_whale_buys() [Helius]      — fresh whale buy signals (last 15 min)
5. get_whale_exits() [Helius]     — detect whales selling open positions
6. fetch_tokens() [DexScreener]  — boosted + new profile candidates
7. fetch_pump_tokens() [pump.fun] — bonding curve candidates
8. enrich() each token            — DexScreener /latest/dex/tokens/{addr}
9. Filter (age/liq/FDV/momentum)
10. score() each token             — rule-based 0–100
11. rug_check() each candidate     — rugcheck.xyz (SOL) / honeypot.is (EVM)
12. format_alert() → tg_send_photo() or tg_send()
13. log_paper_trade()             — add to state["open"] + call sheets_log_open()
14. save_state()                  — write paper_trades.json + git commit + git push
```

---

## Scoring System (rule-based, 0–100)

| Signal | Max pts | Trigger |
|--------|---------|---------|
| 1h price momentum | 25 | >50% → 25, >20% → 18, >5% → 10, >0% → 5 |
| Vol/Liq ratio (24h) | 25 | >30× → 25, >10× → 18, >3× → 10, >1× → 5 |
| Buy/Sell ratio (1h) | 20 | >3 → 20, >2 → 15, >1.5 → 10, >1 → 5 |
| Liquidity depth | 15 | >200K → 15, >50K → 10, >20K → 6, >5K → 3 |
| Source bonus | 18 | whale_buy +18, pump.fun +10, smart_money +12, boost +8 |
| Whale win-rate bonus | 5 | ≥85% WR +5 |
| Age bonus | 7 | <60m → 7, <180m → 3 |
| Pump progress | 5 | 20–70% → 5 |
| **ALERT threshold** | **65** | BUY ≥65, WATCH ≥45, AVOID <45 |

---

## Trade Risk Management

| Parameter | Value | Variable |
|-----------|-------|----------|
| Fixed stop loss | -15% | STOP_LOSS_PCT |
| Trailing activates | +15% gain | TRAIL_ACTIVATE_PCT |
| Trailing gap | 10% below peak | TRAIL_PCT |
| Hard take-profit | +60% | HARD_TP_PCT |
| Max open trades | 8 | MAX_OPEN_TRADES |
| Cooldown after exit | 120 min | COOLDOWN_MINUTES |

Exit statuses: `OPEN` → `TP` / `TSL` / `HARD_TP` / `SL`

---

## Token Filters

| Filter | Value | Notes |
|--------|-------|-------|
| Max age | 120 min | fresher = higher upside |
| Min liquidity | $25K | low liq = easy dump |
| Max FDV | $5M | skip already-pumped |
| Min 1h momentum | 20% | or smart money override |
| Pump.fun mcap | $10K–80% progress | <90 min old, ≥15 trades |

---

## Paper Trade Object Schema

```json
{
  "symbol": "TOKEN",
  "chain": "solana|ethereum|bsc|base|arbitrum|xlayer",
  "address": "0x...",
  "source": "new|boost|pump.fun|whale_buy",
  "score": 72,
  "entry_price": 0.0001267,
  "peak_price": 0.000155,
  "entry_time": "2026-04-24T10:00:00+00:00",
  "trailing_active": true,
  "current_pct": 9.63,
  "status": "open",
  "exit_price": null,
  "exit_pct": null,
  "exit_time": null,
  "api_failures": 0
}
```

---

## Environment Variables

```bash
# Required
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Optional — enables features
HELIUS_API_KEY=          # whale tracking (Solana)
ANTHROPIC_API_KEY=       # Claude AI scoring (scorer.py)
OKX_API_KEY=             # OKX OnchainOS DEX data
OKX_SECRET_KEY=
OKX_PASSPHRASE=
MOLTBOOK_API_KEY=        # post reports to m/buildx

# NEW — Google Sheets trade log
GOOGLE_SHEETS_CREDENTIALS=  # full service account JSON as string
GOOGLE_SHEET_ID=             # spreadsheet ID from URL

# NEW — Twitter/X alerts
TWITTER_API_KEY=
TWITTER_API_SECRET=
TWITTER_ACCESS_TOKEN=
TWITTER_ACCESS_SECRET=
```

---

## Key Functions Quick-Reference

| Function | File | Purpose |
|----------|------|---------|
| `main()` | scan_and_alert.py | full scan loop |
| `check_exits()` | scan_and_alert.py | trailing stop engine |
| `log_paper_trade()` | scan_and_alert.py | open new paper position |
| `_close_pos()` | scan_and_alert.py | move open → closed |
| `fetch_tokens()` | scan_and_alert.py | DexScreener boosted + new |
| `fetch_pump_tokens()` | scan_and_alert.py | pump.fun bonding curves |
| `enrich()` | scan_and_alert.py | DexScreener /latest enrichment |
| `score()` | scan_and_alert.py | rule-based scoring |
| `rug_check()` | scan_and_alert.py | safety check (SOL + EVM) |
| `format_alert()` | scan_and_alert.py | Telegram HTML alert card |
| `tg_send()` / `tg_send_photo()` | scan_and_alert.py | Telegram dispatch |
| `save_state()` | scan_and_alert.py | write JSON + git commit/push |
| `get_whale_buys()` | whales.py | Helius whale signal scanner |
| `post_to_moltbook()` | reporter.py | Moltbook community post |
| `sheets_log_open()` | sheets_logger.py | log trade entry to Google Sheets |
| `sheets_log_close()` | sheets_logger.py | update exit data in Google Sheets |
| `post_tweet()` | twitter_alerts.py | post signal to Twitter/X |

---

## GitHub Actions Workflows

| Workflow | Schedule | Script |
|----------|----------|--------|
| monitor.yml | every 5 min | scan_and_alert.py |
| monitor-positions.yml | every 5 min | check_positions.py |
| commands.yml | every 5 min | commands.py |
| external-trigger.yml | on-demand | manual trigger |
| keepalive.yml | periodic | uptime keepalive |

---

## Current TODO / In-Progress

- [x] Core scanning + paper trading
- [x] Trailing stop engine
- [x] Whale tracking (Helius)
- [x] Rug/honeypot safety checks
- [x] Telegram alerts + commands
- [x] Moltbook reporting
- [x] `sheets_logger.py` — Google Sheets play log (created, needs wiring into scan_and_alert.py)
- [ ] `twitter_alerts.py` — Twitter/X alert posts (not yet created)
- [ ] Wire both into scan_and_alert.py (imports + call sites)
- [ ] Update requirements.txt (gspread, google-auth-oauthlib, tweepy)
- [ ] Update .env.example with new vars

---

## Chains Supported

| Chain | chainId | Notes |
|-------|---------|-------|
| Solana | solana | rugcheck.xyz safety, GMGN/Photon/BullX links |
| Ethereum | ethereum | honeypot.is, DEXTools/Uniswap links |
| BSC | bsc | honeypot.is |
| Base | base | honeypot.is |
| Arbitrum | arbitrum | honeypot.is |
| X Layer | xlayer | OKX OnchainOS, no reliable price feed for exits |

---

## Buy Link Logic

```python
# Solana  → GMGN + Photon + BullX + Trojan + Chart
# EVM     → GMGN + DEXTools + Uniswap + Chart
# X Layer → OKX DEX
```

---

## Typical Prompt Templates (copy-paste into new chats)

### Start a new session on this project:
```
I'm working on Signal Scout — a Python crypto signal bot. 
The CLAUDE.md in the repo root has full context. 
Here's what I need today: [YOUR TASK]
```

### Ask for a targeted change:
```
In scan_and_alert.py, in the [FUNCTION NAME] function around line [LINE],
I need to [CHANGE]. Don't touch anything else.
```

### Debug a GitHub Actions failure:
```
Signal Scout GitHub Actions run failed. Here's the error log: [PASTE LOG]
The relevant file is [FILE]. What broke and how do I fix it?
```

---

## Rules for Claude Working on This Project

1. **Never read the whole codebase** — use CLAUDE.md + targeted reads of specific functions
2. **All HTTP is done with curl subprocess** — no requests library (not in requirements)
3. **No external DB** — state lives in paper_trades.json, committed to git
4. **Graceful degradation** — all new module imports must use try/except so the bot never crashes if an optional dep is missing
5. **GitHub Actions = stateless** — every run starts fresh; state must be loaded from JSON
6. **Keep requirements.txt minimal** — only add deps that are truly needed
7. **Never hardcode secrets** — always read from os.environ / os.getenv
8. **New features → new file** — don't bloat scan_and_alert.py further; add a module and import it
