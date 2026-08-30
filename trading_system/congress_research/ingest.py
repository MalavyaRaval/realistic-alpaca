"""Parses cached raw filer records into congress_transactions rows.

Deliberate exclusions, not oversights:
  - `ret_since`, `excess_since`, `ret_30d`, `ret_1y` (performance/return
    fields the upstream source computes) are stripped out - not just left
    unmapped to a column, but actually removed before anything is
    archived, including the raw-record audit copy. This module tracks
    WHAT was disclosed and WHEN, not whether the trade "worked out" -
    keeping those numbers anywhere retrievable, even in an audit blob,
    would embed exactly the performance-continuation framing this
    research module must avoid.
  - Only `branch == "congress"` records are ingested (House + Senate
    members). Executive-branch officials covered by the same upstream
    dataset (OGE filings) are out of scope for "congressional
    transactions" and are skipped.
  - A record missing `transaction_date` or `filing_date` is skipped
    entirely (logged, not silently dropped) rather than guessing either
    date - disclosure_age_days is meaningless without both. Same for a
    record where filing_date precedes transaction_date (a negative
    disclosure age is a data error, not a real value to store).
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path

from database import init_db, upsert_transactions
from fetch_source import FILER_DIR

SKIP_LOG_PATH = Path(__file__).resolve().parent / "data" / "skipped_records.log"

# Never read into a column, and actively scrubbed from the archived raw
# record too - see module docstring.
_PERFORMANCE_FIELDS = ("ret_since", "excess_since", "ret_30d", "ret_1y")

_MISSING = object()


def _first_present(*values):
    """Like `a or b`, but treats a real falsy value (0, "", False) as
    present rather than skipping to the next fallback - only an actual
    None/absence continues to the next value."""
    for v in values:
        if v is not None:
            return v
    return None


def _parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date() if s else None


def _scrub_performance_fields(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in _PERFORMANCE_FIELDS}


def parse_record(raw: dict, source_file: str, filer: dict = None) -> dict:
    """Returns a row dict ready for database.upsert_transactions, or None
    if the record can't be reliably placed on a disclosure-age timeline.

    `filer` is the per-filer metadata object (branch/chamber/party/state/
    office/full_name) - in the source's per-filer file format these live
    one level up from each trade record, not duplicated onto it. Falls
    back to reading those fields directly off `raw` for the (differently
    shaped) flat aggregate file format, where they ARE embedded per-trade.
    """
    filer = filer or {}
    txn_date = _parse_date(raw.get("transaction_date"))
    filing_date = _parse_date(raw.get("filing_date"))
    if txn_date is None or filing_date is None:
        return None, f"{source_file}: missing transaction_date or filing_date (id={raw.get('id')})"

    disclosure_age_days = (filing_date - txn_date).days
    if disclosure_age_days < 0:
        return None, (
            f"{source_file}: filing_date ({filing_date}) precedes transaction_date "
            f"({txn_date}) (id={raw.get('id')}) - skipped rather than storing a negative age"
        )

    is_late = raw.get("is_late")
    is_late_filing = bool(is_late) if is_late is not None else None

    row = {
        "id": raw["id"],
        "source": raw.get("source_id"),
        "chamber": _first_present(raw.get("chamber"), filer.get("chamber")),
        "politician_name": _first_present(raw.get("filer_name"), filer.get("full_name")),
        "politician_id": _first_present(raw.get("filer_id"), filer.get("id")),
        "party": _first_present(raw.get("party"), filer.get("party")),
        "state": _first_present(raw.get("state"), filer.get("state")),
        "office": _first_present(raw.get("office"), filer.get("office")),
        "owner": raw.get("owner"),
        "transaction_date": txn_date.isoformat(),
        "disclosure_date": filing_date.isoformat(),
        "disclosure_age_days": disclosure_age_days,
        "source_reported_days_to_file": raw.get("days_to_file"),
        "is_late_filing": is_late_filing,
        "ticker": raw.get("ticker"),
        "security_name": raw.get("asset_name"),
        "asset_type": raw.get("asset_type"),
        "transaction_type": raw.get("transaction_type"),
        "amount_range_low": raw.get("amount_range_low"),
        "amount_range_high": raw.get("amount_range_high"),
        "amount_range_label": raw.get("amount_range_label"),
        "comment": raw.get("comment"),
        "source_document_url": raw.get("doc_url"),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "raw_json": json.dumps({
            "trade": _scrub_performance_fields(raw),
            "filer": _scrub_performance_fields(filer),
        }),
    }
    return row, None


def run_ingest():
    init_db()
    filer_files = sorted(FILER_DIR.glob("*.json"))
    if not filer_files:
        raise RuntimeError(
            f"No cached filer files found in {FILER_DIR} - run fetch_source.py first."
        )

    all_rows = []
    skipped = []
    congress_records = 0
    non_congress_skipped = 0
    missing_id_skipped = 0

    for path in filer_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        filer = data.get("filer", {})
        trades = data.get("trades", [])
        for raw in trades:
            branch = raw.get("branch") or filer.get("branch")
            if branch != "congress":
                non_congress_skipped += 1
                continue
            if not raw.get("id"):
                missing_id_skipped += 1
                continue
            congress_records += 1
            row, error = parse_record(raw, path.name, filer=filer)
            if error:
                skipped.append(error)
                continue
            all_rows.append(row)

    inserted = upsert_transactions(all_rows)

    if skipped:
        SKIP_LOG_PATH.write_text("\n".join(skipped), encoding="utf-8")

    print(f"Filer files processed: {len(filer_files)}")
    print(f"Congress-branch records seen: {congress_records}")
    print(f"Non-congress (executive branch) records skipped: {non_congress_skipped}")
    print(f"Records missing an id skipped: {missing_id_skipped}")
    print(f"Records skipped for missing transaction/filing date: {len(skipped)}"
          + (f" (see {SKIP_LOG_PATH})" if skipped else ""))
    print(f"Rows upserted into database: {inserted}")


if __name__ == "__main__":
    run_ingest()
