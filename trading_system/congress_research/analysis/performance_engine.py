"""Per-politician performance statistics, computed only from information
that would have been available at a given evaluation date.

The one rule everything here obeys: a transaction is only visible to this
engine if its disclosure_date <= evaluation_date. A transaction that
happened before evaluation_date but wasn't yet disclosed is invisible -
exactly as it would have been to any real outside observer at that time.
This is what makes the walk-forward analysis (see walk_forward.py)
meaningful rather than just re-running the same lookahead-biased
computation at different labels.

Every return is computed TWO ways, and the difference between them is the
whole point of this module:
  - "immediate": entry/exit priced at the TRANSACTION date - the
    unrealistic best case where you had the politician's own timing.
  - "lagged": entry/exit priced at the DISCLOSURE date - the realistic
    case for anyone trying to copy the trade after actually finding out
    about it. Comparing the two is what "does the apparent gap survive
    disclosure delay" means concretely - a large immediate figure that
    mostly disappears once lagged is exactly the pattern a purely
    disclosure-timing artifact would produce, not evidence of anything.

Amounts are disclosed as ranges, never exact dollars - every dollar
figure here is a range midpoint, an approximation stated as such, not a
precise number. A large computed return, in either version, is a
description of a disclosed historical outcome under this approximate
methodology - it is not evidence that a transaction used material
nonpublic information, and it does not imply the same politician's
future trades will do anything similar; nothing in this module computes,
stores, or asserts either claim.
"""

import statistics
import sys
from dataclasses import dataclass, field
from datetime import date as date_cls
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import price_lookup
from position_reconstruction import reconstruct
from sector_map import get_sector, get_sector_benchmark, FUND_OR_TREASURY

MARKET_BENCHMARK = "SPY"
BUY_SELL_TYPES = {"Purchase", "Sale (Full)", "Sale (Partial)"}


@dataclass
class PricedLeg:
    """One matched trade or open lot, after attaching entry/exit prices
    and a benchmark comparison. `usable=False` legs are excluded from
    every aggregate but retained so coverage can be reported honestly."""
    ticker: str
    sector: str
    entry_date: str    # transaction date used to select the entry price (immediate or lagged, per leg)
    exit_date: str
    dollar_amount: float
    holding_period_days: Optional[int]
    is_realized: bool
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    benchmark_entry_price: Optional[float] = None
    benchmark_exit_price: Optional[float] = None
    sector_benchmark_ticker: Optional[str] = None
    sector_entry_price: Optional[float] = None
    sector_exit_price: Optional[float] = None
    year: Optional[str] = None  # transaction year, for the by-year breakdown
    usable: bool = True
    skip_reason: Optional[str] = None

    @property
    def ret(self) -> Optional[float]:
        if self.entry_price is None or self.exit_price is None or self.entry_price <= 0:
            return None
        return (self.exit_price / self.entry_price) - 1

    @property
    def benchmark_ret(self) -> Optional[float]:
        if self.benchmark_entry_price is None or self.benchmark_exit_price is None or self.benchmark_entry_price <= 0:
            return None
        return (self.benchmark_exit_price / self.benchmark_entry_price) - 1

    @property
    def sector_ret(self) -> Optional[float]:
        if self.sector_entry_price is None or self.sector_exit_price is None or self.sector_entry_price <= 0:
            return None
        return (self.sector_exit_price / self.sector_entry_price) - 1


def _price_leg(ticker: str, entry_date: str, exit_date: str) -> tuple:
    entry_price = price_lookup.get_price_on_or_before(ticker, entry_date)
    exit_price = price_lookup.get_price_on_or_before(ticker, exit_date)
    return entry_price, exit_price


def _build_legs(transactions_by_ticker: dict, evaluation_date: str, lagged: bool) -> list:
    legs = []
    for ticker, txns in transactions_by_ticker.items():
        sector = get_sector(ticker)
        result = reconstruct(txns)

        for trade in result.realized_trades:
            entry_date = trade.buy_disclosure_date if lagged else trade.buy_transaction_date
            exit_date = trade.sell_disclosure_date if lagged else trade.sell_transaction_date
            leg = PricedLeg(
                ticker=ticker, sector=sector, entry_date=entry_date, exit_date=exit_date,
                dollar_amount=trade.dollar_amount, holding_period_days=trade.holding_period_days,
                is_realized=True, year=trade.buy_transaction_date[:4],
            )
            if exit_date > evaluation_date or entry_date > evaluation_date:
                leg.usable = False
                leg.skip_reason = "entry or exit falls after the evaluation date"
                legs.append(leg)
                continue
            entry_price, exit_price = _price_leg(ticker, entry_date, exit_date)
            leg.entry_price, leg.exit_price = entry_price, exit_price
            bench_entry, bench_exit = _price_leg(MARKET_BENCHMARK, entry_date, exit_date)
            leg.benchmark_entry_price, leg.benchmark_exit_price = bench_entry, bench_exit
            sector_etf = get_sector_benchmark(sector)
            if sector_etf:
                s_entry, s_exit = _price_leg(sector_etf, entry_date, exit_date)
                leg.sector_benchmark_ticker, leg.sector_entry_price, leg.sector_exit_price = sector_etf, s_entry, s_exit
            if entry_price is None or exit_price is None:
                leg.usable = False
                leg.skip_reason = "no price data for entry or exit date"
            legs.append(leg)

        for lot in result.open_lots:
            entry_date = lot.disclosure_date if lagged else lot.transaction_date
            if entry_date > evaluation_date:
                continue  # not yet visible as of this evaluation date at all
            exit_date = evaluation_date  # mark-to-market AS OF the evaluation date, never "today"
            leg = PricedLeg(
                ticker=ticker, sector=sector, entry_date=entry_date, exit_date=exit_date,
                dollar_amount=lot.remaining_amount, holding_period_days=None,  # still open - not a completed holding period
                is_realized=False, year=lot.transaction_date[:4],
            )
            entry_price, exit_price = _price_leg(ticker, entry_date, exit_date)
            leg.entry_price, leg.exit_price = entry_price, exit_price
            bench_entry, bench_exit = _price_leg(MARKET_BENCHMARK, entry_date, exit_date)
            leg.benchmark_entry_price, leg.benchmark_exit_price = bench_entry, bench_exit
            sector_etf = get_sector_benchmark(sector)
            if sector_etf:
                s_entry, s_exit = _price_leg(sector_etf, entry_date, exit_date)
                leg.sector_benchmark_ticker, leg.sector_entry_price, leg.sector_exit_price = sector_etf, s_entry, s_exit
            if entry_price is None or exit_price is None:
                leg.usable = False
                leg.skip_reason = "no price data for entry date or evaluation date"
            legs.append(leg)

    return legs


def _weighted_mean(pairs) -> Optional[float]:
    """pairs: iterable of (value, weight). None values are skipped."""
    total_weight = 0.0
    total = 0.0
    for value, weight in pairs:
        if value is None or weight is None or weight <= 0:
            continue
        total += value * weight
        total_weight += weight
    return (total / total_weight) if total_weight > 0 else None


def _max_drawdown(legs: list) -> Optional[float]:
    """Builds a synthetic, proportionally-weighted cumulative-return index
    from the usable legs, ordered by exit/mark date, and returns its worst
    peak-to-trough decline. This is a simplified approximation - it is NOT
    a reconstruction of a real fund's actual NAV (this analysis has no way
    to know real capital allocation timing), just a way to give "maximum
    drawdown" a computable, consistent meaning across politicians."""
    usable = [l for l in legs if l.usable and l.ret is not None]
    if not usable:
        return None
    total_dollars = sum(l.dollar_amount for l in usable)
    if total_dollars <= 0:
        return None

    ordered = sorted(usable, key=lambda l: l.exit_date)
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for leg in ordered:
        weight = leg.dollar_amount / total_dollars
        equity *= (1 + leg.ret * weight)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = min(max_dd, (equity / peak) - 1)
    return max_dd


def _concentration_hhi(legs: list) -> Optional[float]:
    by_ticker = {}
    for leg in legs:
        by_ticker[leg.ticker] = by_ticker.get(leg.ticker, 0.0) + leg.dollar_amount
    total = sum(by_ticker.values())
    if total <= 0:
        return None
    return sum((amount / total) ** 2 for amount in by_ticker.values())


@dataclass
class PoliticianPerformance:
    politician_id: str
    politician_name: str
    evaluation_date: str

    number_of_transactions: int = 0
    number_of_unique_securities: int = 0

    portfolio_return_immediate: Optional[float] = None
    portfolio_return_lagged: Optional[float] = None
    benchmark_return_immediate: Optional[float] = None
    benchmark_return_lagged: Optional[float] = None
    excess_return_immediate: Optional[float] = None
    excess_return_lagged: Optional[float] = None

    max_drawdown_immediate: Optional[float] = None
    concentration_hhi: Optional[float] = None
    avg_holding_period_days: Optional[float] = None
    num_holding_period_observations: int = 0
    transaction_frequency_per_year: Optional[float] = None
    mean_disclosure_age_days: Optional[float] = None
    median_disclosure_age_days: Optional[float] = None

    performance_by_sector: dict = field(default_factory=dict)
    performance_by_year: dict = field(default_factory=dict)

    dollar_weighted_coverage_pct: Optional[float] = None
    n_legs_usable: int = 0
    n_legs_total: int = 0


def compute_performance(politician_id: str, politician_name: str, transactions: list, evaluation_date: str) -> PoliticianPerformance:
    """`transactions` is expected to already be filtered to
    disclosure_date <= evaluation_date by the caller (see
    walk_forward.py), but that filter is re-applied here too - the single
    most important invariant in this whole module is not something to
    trust the caller alone to get right."""
    perf = PoliticianPerformance(politician_id=politician_id, politician_name=politician_name, evaluation_date=evaluation_date)

    transactions = [t for t in transactions if t.disclosure_date <= evaluation_date]
    buy_sell = [t for t in transactions if t.transaction_type in BUY_SELL_TYPES]
    perf.number_of_transactions = len(buy_sell)
    perf.number_of_unique_securities = len({t.ticker for t in buy_sell if t.ticker})

    ages = [t.disclosure_age_days for t in buy_sell if t.disclosure_age_days is not None]
    if ages:
        perf.mean_disclosure_age_days = statistics.mean(ages)
        perf.median_disclosure_age_days = statistics.median(ages)

    dates = sorted(t.transaction_date for t in buy_sell)
    if len(dates) >= 2:
        span_days = (date_cls.fromisoformat(dates[-1]) - date_cls.fromisoformat(dates[0])).days
        if span_days > 0:
            perf.transaction_frequency_per_year = len(buy_sell) / (span_days / 365.25)

    by_ticker = {}
    for t in buy_sell:
        if not t.ticker:
            continue
        by_ticker.setdefault(t.ticker, []).append(t)
    for ticker_txns in by_ticker.values():
        ticker_txns.sort(key=lambda t: (t.transaction_date, t.id))

    legs_immediate = _build_legs(by_ticker, evaluation_date, lagged=False)
    legs_lagged = _build_legs(by_ticker, evaluation_date, lagged=True)

    perf.n_legs_total = len(legs_immediate)
    usable_immediate = [l for l in legs_immediate if l.usable and l.ret is not None]
    usable_lagged = [l for l in legs_lagged if l.usable and l.ret is not None]
    perf.n_legs_usable = len(usable_immediate)

    total_dollars = sum(l.dollar_amount for l in legs_immediate)
    usable_dollars = sum(l.dollar_amount for l in usable_immediate)
    perf.dollar_weighted_coverage_pct = (usable_dollars / total_dollars) if total_dollars > 0 else None

    perf.portfolio_return_immediate = _weighted_mean((l.ret, l.dollar_amount) for l in usable_immediate)
    perf.benchmark_return_immediate = _weighted_mean((l.benchmark_ret, l.dollar_amount) for l in usable_immediate)
    if perf.portfolio_return_immediate is not None and perf.benchmark_return_immediate is not None:
        perf.excess_return_immediate = perf.portfolio_return_immediate - perf.benchmark_return_immediate

    perf.portfolio_return_lagged = _weighted_mean((l.ret, l.dollar_amount) for l in usable_lagged)
    perf.benchmark_return_lagged = _weighted_mean((l.benchmark_ret, l.dollar_amount) for l in usable_lagged)
    if perf.portfolio_return_lagged is not None and perf.benchmark_return_lagged is not None:
        perf.excess_return_lagged = perf.portfolio_return_lagged - perf.benchmark_return_lagged

    perf.max_drawdown_immediate = _max_drawdown(legs_immediate)
    perf.concentration_hhi = _concentration_hhi(legs_immediate)

    realized_holding_periods = [l.holding_period_days for l in legs_immediate if l.is_realized and l.holding_period_days is not None]
    if realized_holding_periods:
        perf.avg_holding_period_days = statistics.mean(realized_holding_periods)
        perf.num_holding_period_observations = len(realized_holding_periods)

    by_sector = {}
    for leg in usable_immediate:
        by_sector.setdefault(leg.sector, []).append(leg)
    for sector, sector_legs in by_sector.items():
        if sector == FUND_OR_TREASURY:
            continue
        ret = _weighted_mean((l.ret, l.dollar_amount) for l in sector_legs)
        bench = _weighted_mean((l.benchmark_ret, l.dollar_amount) for l in sector_legs)
        excess = (ret - bench) if (ret is not None and bench is not None) else None
        perf.performance_by_sector[sector] = {
            "return": ret, "benchmark_return": bench, "excess_return": excess, "n": len(sector_legs),
        }

    by_year = {}
    for leg in usable_immediate:
        by_year.setdefault(leg.year, []).append(leg)
    for year, year_legs in by_year.items():
        ret = _weighted_mean((l.ret, l.dollar_amount) for l in year_legs)
        bench = _weighted_mean((l.benchmark_ret, l.dollar_amount) for l in year_legs)
        excess = (ret - bench) if (ret is not None and bench is not None) else None
        perf.performance_by_year[year] = {
            "return": ret, "benchmark_return": bench, "excess_return": excess, "n": len(year_legs),
        }

    return perf
