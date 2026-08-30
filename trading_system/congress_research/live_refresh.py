"""Lightweight refresh for the live congress-mirror strategy.

The full research database (database.py's default congress_trades.db,
built via fetch_source.py + ingest.py) covers 2011-present via 446
per-filer requests - too slow and too much traffic to run every 5
minutes in a scheduled job. This module instead pulls the source's
capped "most recent ~5000 trades" flat file in ONE request, which is
more than enough to catch new disclosures promptly, and writes to a
SEPARATE, small database file that's cheap to commit on every cycle.

The deep historical database stays local-only (gitignored, regenerated
on demand) and is what backtests/research should use; this live,
frequently-refreshed one is what the running strategy queries.
"""

import json
from pathlib import Path

import database
from fetch_source import RAW_BASE, _get
from ingest import parse_record

LIVE_DB_PATH = Path(__file__).resolve().parent / "data" / "congress_trades_live.db"


def fetch_recent_trades_flat() -> list:
    return json.loads(_get(f"{RAW_BASE}/trades.json"))


def refresh_live_db() -> int:
    database.DB_PATH = LIVE_DB_PATH
    database.init_db()

    raw_trades = fetch_recent_trades_flat()
    rows = []
    for raw in raw_trades:
        if raw.get("branch") != "congress":
            continue
        if not raw.get("id"):
            continue
        row, error = parse_record(raw, "trades.json (live)")
        if row:
            rows.append(row)

    return database.upsert_transactions(rows)


if __name__ == "__main__":
    n = refresh_live_db()
    print(f"Live database refreshed: {n} congress-branch rows at {LIVE_DB_PATH}")
