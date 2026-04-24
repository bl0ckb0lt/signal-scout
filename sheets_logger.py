"""
Google Sheets trade logger for Signal Scout.

Env vars required:
  GOOGLE_SHEETS_CREDENTIALS  — service account JSON (the full file content as a string)
  GOOGLE_SHEET_ID            — the spreadsheet ID from the URL

The sheet must have a tab named "Plays". If it doesn't exist it will be created
automatically along with a header row.
"""

import os
import json

SHEET_NAME = "Plays"

HEADERS = [
    "Symbol", "Chain", "Address", "Source", "Score",
    "Entry Time (UTC)", "Entry Price ($)",
    "Exit Time (UTC)", "Exit Price ($)", "P&L %", "Status",
    "5m %", "1h %", "6h %", "24h %",
    "Liquidity ($)", "Vol 24h ($)", "Buy/Sell Ratio",
    "Whale Signal",
]


def _get_worksheet():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("[sheets] gspread / google-auth not installed — skipping")
        return None

    creds_json = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
    sheet_id   = os.getenv("GOOGLE_SHEET_ID")
    if not creds_json or not sheet_id:
        return None

    try:
        creds_data = json.loads(creds_json)
        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(creds_data, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheet_id)
    except Exception as e:
        print(f"[sheets] auth error: {e}")
        return None

    # Get or create the Plays worksheet
    try:
        ws = sh.worksheet(SHEET_NAME)
    except Exception:
        ws = sh.add_worksheet(title=SHEET_NAME, rows=1000, cols=len(HEADERS))

    # Write headers if the sheet is empty
    if not ws.row_values(1):
        ws.append_row(HEADERS, value_input_option="RAW")

    return ws


def sheets_log_open(token):
    """Append a row when a paper trade opens."""
    ws = _get_worksheet()
    if not ws:
        return

    sym   = token.get("symbol", "?")
    chain = token.get("chain", "?")
    addr  = token.get("address", "")
    src   = token.get("source", "")
    sc    = token.get("score", 0)
    price = token.get("price_usd", "")
    liq   = token.get("liquidity_usd", "")
    vol24 = token.get("volume_h24", "")
    buys  = token.get("buys_h1") or 0
    sells = token.get("sells_h1") or 1
    bsr   = round(buys / sells, 2) if sells else buys

    from datetime import datetime, timezone
    entry_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    whale = "YES" if (token.get("smart_money") or token.get("whale_label")) else "NO"

    row = [
        sym, chain.upper(), addr, src, sc,
        entry_time, float(price) if price else "",
        "", "", "", "OPEN",
        token.get("price_change_m5", ""),
        token.get("price_change_h1", ""),
        token.get("price_change_h6", ""),
        token.get("price_change_h24", ""),
        float(liq) if liq else "",
        float(vol24) if vol24 else "",
        bsr,
        whale,
    ]

    try:
        ws.append_row(row, value_input_option="USER_ENTERED")
        print(f"[sheets] logged open: {sym}")
    except Exception as e:
        print(f"[sheets] append error: {e}")


def sheets_log_close(pos):
    """Update the matching open row with exit data when a trade closes."""
    ws = _get_worksheet()
    if not ws:
        return

    addr = pos.get("address", "")
    if not addr:
        return

    try:
        # Find the address in column 3 (1-indexed)
        cells = ws.findall(addr, in_column=3)
        if not cells:
            return

        # Pick the last row that still shows OPEN status
        target_row = None
        for cell in reversed(cells):
            status_val = ws.cell(cell.row, 11).value  # column 11 = Status
            if status_val == "OPEN":
                target_row = cell.row
                break

        if not target_row:
            target_row = cells[-1].row  # fallback to last match

        # Columns: 8=Exit Time, 9=Exit Price, 10=P&L%, 11=Status
        ws.update_cell(target_row, 8,  pos.get("exit_time", ""))
        ws.update_cell(target_row, 9,  pos.get("exit_price", ""))
        ws.update_cell(target_row, 10, pos.get("exit_pct", ""))
        ws.update_cell(target_row, 11, pos.get("status", ""))
        print(f"[sheets] logged close: {pos.get('symbol')} → {pos.get('status')} {pos.get('exit_pct','')}%")
    except Exception as e:
        print(f"[sheets] update error: {e}")
