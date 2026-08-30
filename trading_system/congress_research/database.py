"""SQLite database for disclosed congressional stock transactions.

Read this before using this data anywhere near a strategy:

  - This is DISCLOSURE data, not market data. `transaction_date` is when
    the trade reportedly happened; `disclosure_date` is when the public
    filing that revealed it was made. They are frequently weeks apart
    (the STOCK Act requires filing within 45 days, and filings are
    routinely late beyond that) - `disclosure_age_days` makes that gap
    explicit on every row. Never treat a row as "this just happened."
  - A transaction being disclosed does NOT mean it was made using material
    nonpublic information. Nothing in this module asserts or implies that.
  - Nothing in this module computes, stores, or implies that any
    politician's past trading outcomes will continue - there is no
    performance/return column here, and the upstream source's own return
    figures are actively stripped out before anything is archived, not
    just left unmapped (see ingest.py's _scrub_performance_fields).
  - Every row keeps `source_document_url`, a link to the specific original
    government filing (House Clerk PDF or Senate eFD record), plus the
    original raw trade + filer records (performance fields scrubbed) in
    `raw_json`, for auditing what this module's parsing produced. Never
    invented, never backfilled with a guess - a missing field in the
    source is NULL here.
  - The Senate eFD portion of this data carries a statutory use
    restriction (5 U.S.C. app. §105(c)): it may not be used for a
    commercial purpose other than news/media dissemination to the general
    public. That applies to this data regardless of how it's stored or
    queried - see the project-level notes on this before using Senate-
    sourced rows for anything beyond personal research/education.
"""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent / "data" / "congress_trades.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS congress_transactions (
    id                      TEXT PRIMARY KEY,
    source                  TEXT NOT NULL,
    chamber                 TEXT NOT NULL,
    politician_name         TEXT NOT NULL,
    politician_id           TEXT,
    party                   TEXT,
    state                   TEXT,
    office                  TEXT,
    owner                   TEXT,
    transaction_date        TEXT NOT NULL,
    disclosure_date         TEXT NOT NULL,
    disclosure_age_days     INTEGER NOT NULL,
    source_reported_days_to_file INTEGER,
    is_late_filing          INTEGER,
    ticker                  TEXT,
    security_name           TEXT,
    asset_type              TEXT,
    transaction_type        TEXT NOT NULL,
    amount_range_low        REAL,
    amount_range_high       REAL,
    amount_range_label      TEXT,
    comment                 TEXT,
    source_document_url     TEXT NOT NULL,
    ingested_at             TEXT NOT NULL,
    raw_json                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticker ON congress_transactions(ticker);
CREATE INDEX IF NOT EXISTS idx_politician ON congress_transactions(politician_name);
CREATE INDEX IF NOT EXISTS idx_transaction_date ON congress_transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_disclosure_date ON congress_transactions(disclosure_date);
"""


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


@dataclass
class CongressTransaction:
    id: str
    source: str
    chamber: str
    politician_name: str
    politician_id: Optional[str]
    party: Optional[str]
    state: Optional[str]
    office: Optional[str]
    owner: Optional[str]
    transaction_date: str
    disclosure_date: str
    disclosure_age_days: int
    source_reported_days_to_file: Optional[int]
    is_late_filing: Optional[bool]
    ticker: Optional[str]
    security_name: Optional[str]
    asset_type: Optional[str]
    transaction_type: str
    amount_range_low: Optional[float]
    amount_range_high: Optional[float]
    amount_range_label: Optional[str]
    comment: Optional[str]
    source_document_url: str
    ingested_at: str


_COLUMNS = [
    "id", "source", "chamber", "politician_name", "politician_id", "party",
    "state", "office", "owner", "transaction_date", "disclosure_date",
    "disclosure_age_days", "source_reported_days_to_file", "is_late_filing",
    "ticker", "security_name", "asset_type", "transaction_type",
    "amount_range_low", "amount_range_high", "amount_range_label",
    "comment", "source_document_url", "ingested_at", "raw_json",
]


def upsert_transactions(records: list) -> int:
    """records: list of dicts with keys matching _COLUMNS (raw_json
    included). Upserts by id so re-running ingestion is idempotent."""
    if not records:
        return 0
    placeholders = ", ".join(f":{c}" for c in _COLUMNS)
    columns = ", ".join(_COLUMNS)
    update_clause = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c != "id")
    sql = (
        f"INSERT INTO congress_transactions ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {update_clause}"
    )
    with connect() as conn:
        conn.executemany(sql, records)
    return len(records)


def _row_to_transaction(row: sqlite3.Row) -> CongressTransaction:
    return CongressTransaction(**{c: row[c] for c in _COLUMNS if c != "raw_json"})


def query_transactions(
    ticker: Optional[str] = None,
    politician_name: Optional[str] = None,
    chamber: Optional[str] = None,
    since_transaction_date: Optional[str] = None,
    until_transaction_date: Optional[str] = None,
    min_disclosure_age_days: Optional[int] = None,
    limit: int = 1000,
) -> list:
    """Read-only query interface.

    Every returned CongressTransaction carries both `transaction_date` and
    `disclosure_date`, plus `disclosure_age_days` - check that field
    before treating any row as current. Results default to newest-TRADED-
    first (`ORDER BY transaction_date DESC`), which is NOT the same as
    newest-disclosed-first: a row at the top of an unfiltered query can
    still have a large disclosure_age_days if it was filed late. Use
    min_disclosure_age_days to filter by reporting lag directly.

    All date params are ISO 'YYYY-MM-DD' strings compared against
    transaction_date (not disclosure_date) unless otherwise noted.
    """
    clauses = []
    params = {}
    if ticker:
        clauses.append("ticker = :ticker")
        params["ticker"] = ticker.upper()
    if politician_name:
        clauses.append("politician_name LIKE :politician_name")
        params["politician_name"] = f"%{politician_name}%"
    if chamber:
        clauses.append("chamber = :chamber")
        params["chamber"] = chamber
    if since_transaction_date:
        clauses.append("transaction_date >= :since")
        params["since"] = since_transaction_date
    if until_transaction_date:
        clauses.append("transaction_date <= :until")
        params["until"] = until_transaction_date
    if min_disclosure_age_days is not None:
        clauses.append("disclosure_age_days >= :min_age")
        params["min_age"] = min_disclosure_age_days

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        f"SELECT {', '.join(_COLUMNS)} FROM congress_transactions {where} "
        f"ORDER BY transaction_date DESC LIMIT :limit"
    )
    params["limit"] = limit

    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_transaction(r) for r in rows]


def get_raw_record(transaction_id: str) -> Optional[dict]:
    """Returns the preserved original source record (trade + filer,
    performance fields scrubbed - see ingest.py) for one transaction, for
    auditing what this database's parsing produced from it."""
    with connect() as conn:
        row = conn.execute(
            "SELECT raw_json FROM congress_transactions WHERE id = ?", (transaction_id,)
        ).fetchone()
    return json.loads(row["raw_json"]) if row else None


def get_disclosure_lag_stats() -> dict:
    """Descriptive statistics about reporting delay ONLY - no performance
    or return statistics are computed anywhere in this module.

    late_count/unknown_lateness_count only cover rows where the source
    actually reported is_late; SUM() over a boolean column skips NULLs
    rather than silently counting "unknown" as "on time"."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as n,
                AVG(disclosure_age_days) as mean_days,
                MIN(disclosure_age_days) as min_days,
                MAX(disclosure_age_days) as max_days,
                SUM(is_late_filing) as late_count,
                SUM(CASE WHEN is_late_filing IS NULL THEN 1 ELSE 0 END) as unknown_lateness_count
            FROM congress_transactions
            """
        ).fetchone()
    return dict(row)


def get_summary() -> dict:
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM congress_transactions").fetchone()[0]
        date_range = conn.execute(
            "SELECT MIN(transaction_date), MAX(transaction_date) FROM congress_transactions"
        ).fetchone()
        disclosure_range = conn.execute(
            "SELECT MIN(disclosure_date), MAX(disclosure_date) FROM congress_transactions"
        ).fetchone()
        politicians = conn.execute(
            "SELECT COUNT(DISTINCT politician_name) FROM congress_transactions"
        ).fetchone()[0]
        by_chamber = conn.execute(
            "SELECT chamber, COUNT(*) FROM congress_transactions GROUP BY chamber"
        ).fetchall()
    return {
        "total_transactions": n,
        "transaction_date_range": list(date_range),
        "disclosure_date_range": list(disclosure_range),
        "distinct_politicians": politicians,
        "by_chamber": {r[0]: r[1] for r in by_chamber},
    }
