"""Tests for the point-in-time price lookup - the single most safety-
critical primitive in this analysis, since a lookahead bug here would
silently corrupt every downstream return statistic."""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import price_lookup


def write_fixture(tmp_path, ticker, rows):
    path = tmp_path / f"{ticker}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        for date, close in rows:
            writer.writerow({"date": date, "open": close, "high": close, "low": close, "close": close, "volume": 1000})


def use_fixture_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(price_lookup, "PRICES_DIR", tmp_path)
    price_lookup.clear_cache()


def test_exact_date_match_returns_that_close(tmp_path, monkeypatch):
    use_fixture_dir(monkeypatch, tmp_path)
    write_fixture(tmp_path, "AAPL", [("2024-01-02", 100.0), ("2024-01-03", 101.0), ("2024-01-04", 102.0)])
    assert price_lookup.get_price_on_or_before("AAPL", "2024-01-03") == 101.0


def test_weekend_or_holiday_falls_back_to_prior_trading_day_not_next(tmp_path, monkeypatch):
    use_fixture_dir(monkeypatch, tmp_path)
    # 2024-01-06/07 is a weekend - no bar exists for it
    write_fixture(tmp_path, "AAPL", [("2024-01-05", 100.0), ("2024-01-08", 105.0)])
    # a request for the weekend date must return Friday's close, never Monday's
    assert price_lookup.get_price_on_or_before("AAPL", "2024-01-06") == 100.0
    assert price_lookup.get_price_on_or_before("AAPL", "2024-01-07") == 100.0


def test_date_before_any_data_returns_none_not_the_earliest_bar(tmp_path, monkeypatch):
    use_fixture_dir(monkeypatch, tmp_path)
    write_fixture(tmp_path, "AAPL", [("2024-06-01", 100.0), ("2024-06-02", 101.0)])
    # a lookahead bug would wrongly return the earliest available price here
    assert price_lookup.get_price_on_or_before("AAPL", "2024-01-01") is None


def test_date_after_all_data_returns_the_latest_bar(tmp_path, monkeypatch):
    use_fixture_dir(monkeypatch, tmp_path)
    write_fixture(tmp_path, "AAPL", [("2024-01-01", 100.0), ("2024-01-02", 101.0)])
    assert price_lookup.get_price_on_or_before("AAPL", "2030-01-01") == 101.0


def test_never_returns_a_price_from_a_later_date(tmp_path, monkeypatch):
    use_fixture_dir(monkeypatch, tmp_path)
    write_fixture(tmp_path, "AAPL", [
        ("2024-01-01", 100.0), ("2024-01-02", 50.0), ("2024-01-03", 200.0),
    ])
    # a request for 2024-01-02 must never see the 2024-01-03 spike to 200
    assert price_lookup.get_price_on_or_before("AAPL", "2024-01-02") == 50.0


def test_unknown_ticker_returns_none(tmp_path, monkeypatch):
    use_fixture_dir(monkeypatch, tmp_path)
    assert price_lookup.get_price_on_or_before("NOPE", "2024-01-01") is None
    assert price_lookup.has_price_data("NOPE") is False


def test_earliest_available_date(tmp_path, monkeypatch):
    use_fixture_dir(monkeypatch, tmp_path)
    write_fixture(tmp_path, "AAPL", [("2024-03-01", 100.0), ("2024-03-02", 101.0)])
    assert price_lookup.earliest_available_date("AAPL") == "2024-03-01"
    assert price_lookup.earliest_available_date("NOPE") is None
