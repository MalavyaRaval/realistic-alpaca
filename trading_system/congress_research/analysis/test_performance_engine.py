"""Tests for the per-politician performance engine - synthetic prices and
transactions with known, hand-computable expected results. The
walk-forward/no-lookahead tests here matter more than most: a bug would
silently produce a confidently wrong "top performer" ranking."""

import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import performance_engine
import price_lookup
from performance_engine import compute_performance


@dataclass
class FakeTxn:
    id: str
    ticker: Optional[str]
    transaction_date: str
    disclosure_date: str
    disclosure_age_days: Optional[int]
    transaction_type: str
    amount_range_low: Optional[float]
    amount_range_high: Optional[float]


def txn(id, ticker, tdate, ddate, ttype, low, high):
    age = None
    if tdate and ddate:
        from datetime import date
        age = (date.fromisoformat(ddate) - date.fromisoformat(tdate)).days
    return FakeTxn(id=id, ticker=ticker, transaction_date=tdate, disclosure_date=ddate,
                   disclosure_age_days=age, transaction_type=ttype,
                   amount_range_low=low, amount_range_high=high)


def write_prices(tmp_path, ticker, rows):
    path = tmp_path / f"{ticker}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
        w.writeheader()
        for d, c in rows:
            w.writerow({"date": d, "open": c, "high": c, "low": c, "close": c, "volume": 1000})


def setup_prices(monkeypatch, tmp_path):
    monkeypatch.setattr(price_lookup, "PRICES_DIR", tmp_path)
    price_lookup.clear_cache()


def test_basic_realized_return_and_excess_return(tmp_path, monkeypatch):
    setup_prices(monkeypatch, tmp_path)
    write_prices(tmp_path, "AAPL", [("2024-01-01", 100.0), ("2024-06-01", 150.0)])
    write_prices(tmp_path, "SPY", [("2024-01-01", 400.0), ("2024-06-01", 440.0)])

    txns = [
        txn("b1", "AAPL", "2024-01-01", "2024-01-15", "Purchase", 1001, 15000),
        txn("s1", "AAPL", "2024-06-01", "2024-06-10", "Sale (Full)", 1001, 15000),
    ]
    perf = compute_performance("p1", "Test Person", txns, evaluation_date="2024-12-31")

    assert perf.portfolio_return_immediate == 0.5  # 150/100 - 1
    assert abs(perf.benchmark_return_immediate - 0.1) < 1e-9  # 440/400 - 1
    assert abs(perf.excess_return_immediate - 0.4) < 1e-9


def test_lagged_return_uses_disclosure_date_prices_and_differs_from_immediate(tmp_path, monkeypatch):
    setup_prices(monkeypatch, tmp_path)
    # price spikes right after the trade, before it's disclosed - an
    # immediate return would see the spike; a lagged (copier) return would not.
    write_prices(tmp_path, "AAPL", [
        ("2024-01-01", 100.0),   # transaction date price
        ("2024-01-15", 130.0),   # disclosure date price - already moved
        ("2024-06-01", 150.0),   # sell transaction date price
        ("2024-06-10", 152.0),   # sell disclosure date price
    ])
    write_prices(tmp_path, "SPY", [
        ("2024-01-01", 400.0), ("2024-01-15", 400.0),
        ("2024-06-01", 400.0), ("2024-06-10", 400.0),
    ])

    txns = [
        txn("b1", "AAPL", "2024-01-01", "2024-01-15", "Purchase", 1001, 15000),
        txn("s1", "AAPL", "2024-06-01", "2024-06-10", "Sale (Full)", 1001, 15000),
    ]
    perf = compute_performance("p1", "Test Person", txns, evaluation_date="2024-12-31")

    assert perf.portfolio_return_immediate == (150.0 / 100.0) - 1   # 0.5
    assert perf.portfolio_return_lagged == (152.0 / 130.0) - 1       # ~0.1538
    assert perf.portfolio_return_lagged < perf.portfolio_return_immediate


def test_disclosed_after_evaluation_date_is_completely_invisible(tmp_path, monkeypatch):
    setup_prices(monkeypatch, tmp_path)
    write_prices(tmp_path, "AAPL", [("2024-01-01", 100.0), ("2024-06-01", 999.0)])
    write_prices(tmp_path, "SPY", [("2024-01-01", 400.0), ("2024-06-01", 400.0)])

    # This whole round trip is disclosed AFTER the evaluation date -
    # a walk-forward analysis as of 2024-02-01 must not see it at all.
    txns = [
        txn("b1", "AAPL", "2024-01-01", "2024-06-05", "Purchase", 1001, 15000),
        txn("s1", "AAPL", "2024-06-01", "2024-06-10", "Sale (Full)", 1001, 15000),
    ]
    perf = compute_performance("p1", "Test Person", txns, evaluation_date="2024-02-01")

    assert perf.number_of_transactions == 0
    assert perf.portfolio_return_immediate is None
    assert perf.n_legs_total == 0


def test_open_lot_marked_to_evaluation_date_not_a_later_date(tmp_path, monkeypatch):
    setup_prices(monkeypatch, tmp_path)
    write_prices(tmp_path, "AAPL", [
        ("2024-01-01", 100.0), ("2024-06-01", 120.0), ("2024-12-31", 200.0),
    ])
    write_prices(tmp_path, "SPY", [
        ("2024-01-01", 400.0), ("2024-06-01", 400.0), ("2024-12-31", 400.0),
    ])

    txns = [txn("b1", "AAPL", "2024-01-01", "2024-01-15", "Purchase", 1001, 15000)]
    # evaluate mid-year - must mark at the 2024-06-01 price (120), never see
    # the later 200 price that only exists after the evaluation date.
    perf = compute_performance("p1", "Test Person", txns, evaluation_date="2024-06-01")

    assert perf.portfolio_return_immediate == (120.0 / 100.0) - 1  # 0.2, not 1.0


def test_missing_price_data_excludes_leg_but_is_reflected_in_coverage(tmp_path, monkeypatch):
    setup_prices(monkeypatch, tmp_path)
    write_prices(tmp_path, "AAPL", [("2024-01-01", 100.0), ("2024-06-01", 150.0)])
    write_prices(tmp_path, "SPY", [("2024-01-01", 400.0), ("2024-06-01", 440.0)])
    # NOPRICE has no price file at all - simulates an illiquid/unclassified ticker

    txns = [
        txn("b1", "AAPL", "2024-01-01", "2024-01-15", "Purchase", 1001, 15000),
        txn("s1", "AAPL", "2024-06-01", "2024-06-10", "Sale (Full)", 1001, 15000),
        txn("b2", "NOPRICE", "2024-02-01", "2024-02-15", "Purchase", 1001, 15000),
        txn("s2", "NOPRICE", "2024-07-01", "2024-07-10", "Sale (Full)", 1001, 15000),
    ]
    perf = compute_performance("p1", "Test Person", txns, evaluation_date="2024-12-31")

    assert perf.number_of_transactions == 4
    assert perf.number_of_unique_securities == 2
    # only the AAPL leg contributes to the return - NOPRICE's return is None, excluded
    assert perf.portfolio_return_immediate == 0.5
    # coverage should reflect that half the disclosed dollar volume had no usable price
    assert perf.dollar_weighted_coverage_pct == 0.5


def test_concentration_hhi(tmp_path, monkeypatch):
    setup_prices(monkeypatch, tmp_path)
    write_prices(tmp_path, "AAPL", [("2024-01-01", 100.0), ("2024-06-01", 110.0)])
    write_prices(tmp_path, "MSFT", [("2024-01-01", 200.0), ("2024-06-01", 220.0)])
    write_prices(tmp_path, "SPY", [("2024-01-01", 400.0), ("2024-06-01", 400.0)])

    # equal dollar amounts in two tickers -> HHI = 0.5^2 + 0.5^2 = 0.5
    txns = [
        txn("b1", "AAPL", "2024-01-01", "2024-01-15", "Purchase", 15001, 15001),
        txn("b2", "MSFT", "2024-01-01", "2024-01-15", "Purchase", 15001, 15001),
    ]
    perf = compute_performance("p1", "Test Person", txns, evaluation_date="2024-12-31")
    assert abs(perf.concentration_hhi - 0.5) < 1e-9


def test_avg_holding_period_only_counts_realized_trades(tmp_path, monkeypatch):
    setup_prices(monkeypatch, tmp_path)
    write_prices(tmp_path, "AAPL", [("2024-01-01", 100.0), ("2024-04-01", 110.0)])
    write_prices(tmp_path, "MSFT", [("2024-01-01", 200.0), ("2024-12-31", 210.0)])
    write_prices(tmp_path, "SPY", [("2024-01-01", 400.0), ("2024-04-01", 400.0), ("2024-12-31", 400.0)])

    txns = [
        txn("b1", "AAPL", "2024-01-01", "2024-01-15", "Purchase", 1001, 15000),
        txn("s1", "AAPL", "2024-04-01", "2024-04-10", "Sale (Full)", 1001, 15000),  # realized, 91 days
        txn("b2", "MSFT", "2024-01-01", "2024-01-15", "Purchase", 1001, 15000),      # still open
    ]
    perf = compute_performance("p1", "Test Person", txns, evaluation_date="2024-12-31")
    assert perf.num_holding_period_observations == 1
    assert perf.avg_holding_period_days == 91


def test_performance_by_year_and_sector(tmp_path, monkeypatch):
    setup_prices(monkeypatch, tmp_path)
    write_prices(tmp_path, "AAPL", [("2022-01-01", 100.0), ("2022-06-01", 110.0)])
    write_prices(tmp_path, "XOM", [("2023-01-01", 50.0), ("2023-06-01", 60.0)])
    write_prices(tmp_path, "SPY", [
        ("2022-01-01", 400.0), ("2022-06-01", 400.0),
        ("2023-01-01", 400.0), ("2023-06-01", 400.0),
    ])
    write_prices(tmp_path, "XLK", [("2022-01-01", 100.0), ("2022-06-01", 100.0)])
    write_prices(tmp_path, "XLE", [("2023-01-01", 80.0), ("2023-06-01", 80.0)])

    txns = [
        txn("b1", "AAPL", "2022-01-01", "2022-01-15", "Purchase", 1001, 15000),
        txn("s1", "AAPL", "2022-06-01", "2022-06-10", "Sale (Full)", 1001, 15000),
        txn("b2", "XOM", "2023-01-01", "2023-01-15", "Purchase", 1001, 15000),
        txn("s2", "XOM", "2023-06-01", "2023-06-10", "Sale (Full)", 1001, 15000),
    ]
    perf = compute_performance("p1", "Test Person", txns, evaluation_date="2024-12-31")

    assert set(perf.performance_by_year.keys()) == {"2022", "2023"}
    assert abs(perf.performance_by_year["2022"]["return"] - 0.1) < 1e-9
    assert abs(perf.performance_by_year["2023"]["return"] - 0.2) < 1e-9

    assert "Information Technology" in perf.performance_by_sector
    assert "Energy" in perf.performance_by_sector
    assert abs(perf.performance_by_sector["Information Technology"]["return"] - 0.1) < 1e-9
