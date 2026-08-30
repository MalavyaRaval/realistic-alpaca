"""Unit tests for record parsing and the query API - synthetic records
only, no network access, no dependency on the live database being
populated."""

import json

import pytest

import database
from ingest import parse_record


def make_raw(**overrides):
    base = {
        "id": "senate_test-id_t0",
        "source_id": "senate_efd",
        "transaction_date": "2026-07-01",
        "filing_date": "2026-07-20",
        "owner": "Self",
        "ticker": "AAPL",
        "asset_name": "Apple Inc. Common Stock",
        "asset_type": "Stock",
        "transaction_type": "Purchase",
        "amount_range_low": 15001,
        "amount_range_high": 50000,
        "amount_range_label": "$15,001 - $50,000",
        "days_to_file": 19,
        "is_late": 0,
        "comment": None,
        "filer_id": "senate_test_person",
        "filer_name": "Test Person",
        "branch": "congress",
        "chamber": "senate",
        "party": "D",
        "state": "CA",
        "office": "U.S. Senator · CA",
        "doc_url": "https://efdsearch.senate.gov/search/view/ptr/test-id/",
        "filing_type": "PTR",
        "ret_since": 12.3,
        "excess_since": 4.5,
    }
    base.update(overrides)
    return base


def test_parse_record_computes_disclosure_age_from_dates():
    row, error = parse_record(make_raw(), "test_file.json")
    assert error is None
    assert row["disclosure_age_days"] == 19  # 2026-07-20 minus 2026-07-01
    assert row["transaction_date"] == "2026-07-01"
    assert row["disclosure_date"] == "2026-07-20"
    assert row["transaction_date"] != row["disclosure_date"]


def test_parse_record_preserves_source_document_url():
    row, error = parse_record(make_raw(), "test_file.json")
    assert row["source_document_url"] == "https://efdsearch.senate.gov/search/view/ptr/test-id/"


def test_parse_record_excludes_performance_fields_everywhere():
    row, error = parse_record(make_raw(), "test_file.json")
    assert "ret_since" not in row
    assert "excess_since" not in row
    assert "ret_30d" not in row
    assert "ret_1y" not in row
    # scrubbed from the archived raw record too, not just unmapped to a column -
    # these numbers must not be retrievable from anywhere in this database.
    preserved = json.loads(row["raw_json"])
    assert "ret_since" not in preserved["trade"]
    assert "excess_since" not in preserved["trade"]
    assert "ret_since" not in preserved["filer"]


def test_parse_record_archives_both_trade_and_filer_for_audit():
    filer = {"id": "senate_test_person", "full_name": "Test Person", "chamber": "senate", "party": "D"}
    raw = {k: v for k, v in make_raw().items() if k not in ("chamber", "party", "filer_name")}
    row, error = parse_record(raw, "test_file.json", filer=filer)
    assert error is None
    # values sourced from `filer` (not present on `raw`) end up in the row...
    assert row["chamber"] == "senate"
    assert row["politician_name"] == "Test Person"
    # ...and the filer object that produced them is itself preserved for audit.
    preserved = json.loads(row["raw_json"])
    assert preserved["filer"]["full_name"] == "Test Person"
    assert preserved["trade"]["id"] == raw["id"]


def test_first_present_prefers_a_real_falsy_value_over_a_fallback():
    from ingest import _first_present
    assert _first_present("", "fallback") == ""
    assert _first_present(0, "fallback") == 0
    assert _first_present(False, "fallback") is False
    assert _first_present(None, "fallback") == "fallback"
    assert _first_present(None, None) is None


def test_parse_record_skips_negative_disclosure_age_rather_than_storing_it():
    row, error = parse_record(
        make_raw(transaction_date="2026-07-20", filing_date="2026-07-01"), "test_file.json"
    )
    assert row is None
    assert "precedes" in error


def test_parse_record_skips_when_dates_missing_rather_than_guessing():
    row, error = parse_record(make_raw(transaction_date=None), "test_file.json")
    assert row is None
    assert error is not None
    row2, error2 = parse_record(make_raw(filing_date=None), "test_file.json")
    assert row2 is None
    assert error2 is not None


def test_parse_record_does_not_invent_missing_ticker():
    row, error = parse_record(make_raw(ticker=None, asset_type="Non-Public Stock"), "test_file.json")
    assert row["ticker"] is None
    assert row["asset_type"] == "Non-Public Stock"


def test_parse_record_preserves_null_owner_and_comment():
    row, error = parse_record(make_raw(owner=None, comment=None), "test_file.json")
    assert row["owner"] is None
    assert row["comment"] is None


def test_database_roundtrip_and_query(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()

    row1, _ = parse_record(make_raw(id="t1", ticker="AAPL", transaction_date="2026-06-01", filing_date="2026-06-15"), "f")
    row2, _ = parse_record(make_raw(id="t2", ticker="MSFT", transaction_date="2026-06-10", filing_date="2026-07-25"), "f")
    database.upsert_transactions([row1, row2])

    all_rows = database.query_transactions()
    assert len(all_rows) == 2

    aapl_only = database.query_transactions(ticker="aapl")  # case-insensitive
    assert len(aapl_only) == 1
    assert aapl_only[0].ticker == "AAPL"

    stale_only = database.query_transactions(min_disclosure_age_days=30)
    assert len(stale_only) == 1
    assert stale_only[0].id == "t2"


def test_upsert_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()
    row, _ = parse_record(make_raw(id="dup"), "f")
    database.upsert_transactions([row])
    database.upsert_transactions([row])  # re-ingest same record
    assert len(database.query_transactions()) == 1


def test_get_raw_record_returns_full_original(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()
    raw = make_raw(id="audit-me")
    row, _ = parse_record(raw, "f")
    database.upsert_transactions([row])

    fetched_raw = database.get_raw_record("audit-me")
    assert "ret_since" not in fetched_raw["trade"]  # scrubbed, not just excluded from the schema
    assert fetched_raw["trade"]["doc_url"] == raw["doc_url"]


def test_disclosure_lag_stats_never_touches_performance(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()
    row, _ = parse_record(make_raw(id="lag1", transaction_date="2026-01-01", filing_date="2026-02-20", is_late=1), "f")
    database.upsert_transactions([row])

    stats = database.get_disclosure_lag_stats()
    assert stats["n"] == 1
    assert stats["late_count"] == 1
    assert "ret_since" not in stats
    assert "return" not in str(stats).lower()


def test_get_new_purchase_candidates_with_empty_seen_ids_still_matches_rows(tmp_path, monkeypatch):
    # Regression test: `id NOT IN (NULL)` is a SQL trap that matches
    # nothing at all - this must not happen when seen_ids is empty, which
    # is the normal starting state before anything has been seen yet.
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()
    row, _ = parse_record(make_raw(id="fresh1", chamber="house", transaction_type="Purchase", ticker="NVDA"), "f")
    database.upsert_transactions([row])

    candidates = database.get_new_purchase_candidates(seen_ids=[], chambers=("house",))
    assert len(candidates) == 1
    assert candidates[0].id == "fresh1"


def test_get_new_purchase_candidates_excludes_seen_and_wrong_chamber(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()
    house_new, _ = parse_record(make_raw(id="h1", chamber="house", transaction_type="Purchase", ticker="AAPL"), "f")
    house_seen, _ = parse_record(make_raw(id="h2", chamber="house", transaction_type="Purchase", ticker="MSFT"), "f")
    senate_one, _ = parse_record(make_raw(id="s1", chamber="senate", transaction_type="Purchase", ticker="GOOGL"), "f")
    sale_one, _ = parse_record(make_raw(id="h3", chamber="house", transaction_type="Sale (Full)", ticker="TSLA"), "f")
    database.upsert_transactions([house_new, house_seen, senate_one, sale_one])

    candidates = database.get_new_purchase_candidates(seen_ids=["h2"], chambers=("house",))
    ids = {c.id for c in candidates}
    assert ids == {"h1"}  # h2 seen, s1 wrong chamber, h3 not a Purchase


def test_disclosure_lag_stats_does_not_count_unknown_lateness_as_on_time(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()
    known_late, _ = parse_record(make_raw(id="known", is_late=1), "f")
    unknown, _ = parse_record(make_raw(id="unknown", is_late=None), "f")
    database.upsert_transactions([known_late, unknown])

    stats = database.get_disclosure_lag_stats()
    assert stats["n"] == 2
    assert stats["late_count"] == 1          # NULL must not be folded into "not late"
    assert stats["unknown_lateness_count"] == 1
