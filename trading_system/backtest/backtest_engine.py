"""Backtest engine for the trailing-stop strategy. Pure historical
simulation - never touches Alpaca's trading API, never submits an order.

Methodology, stated up front because every one of these is a real modeling
choice that affects the numbers:

- Uses DAILY OHLC bars, not tick/minute data. A stop is treated as touched
  if the day's LOW reaches or crosses it. If the day's OPEN already gaps
  through the stop, the fill is modeled at the open (worse), not at the
  theoretical stop price - a real stop order can only fill at the next
  available price once triggered, and a gap means that price is the open.
- No lookahead: the stop level in effect for a given day's exit check is
  whatever it was ratcheted to using PRIOR days' highs. Today's high can
  only raise tomorrow's stop, never retroactively justify today's own
  exit check against a stop that hasn't been set yet.
- No same-day entry-then-exit: a fresh entry is not exit-checked on its own
  bar (the stop is only just being established at that day's open).
- The live strategy is single-shot (one entry, done forever). For
  backtesting, whenever the position goes flat it re-enters on the next
  bar - otherwise a multi-year backtest would produce exactly one trade,
  which can't support win rate / profit factor / average win-loss at all.
  This evaluates the STOP-MANAGEMENT RULES across many independent trade
  instances, not "what would have happened to one specific trade."
- Full reinvestment: 100% of equity is deployed on every entry, 100%
  liquidated on every exit (no partial sizing, no cash drag between
  trades). Whatever position is still open at the end of the window is
  closed at the final bar's close for reporting purposes.
"""

from dataclasses import dataclass, field
from datetime import date as date_cls
from typing import Optional

STARTING_CAPITAL = 100_000.0

# Cost assumptions - see the report for sourcing/rationale. Alpaca charges
# $0 commission on US equities, so that part is fact, not an assumption.
# Actually deducted per trade leg below - not dead code, so a different
# broker's non-zero commission would flow through correctly if changed.
COMMISSION = 0.0
HALF_SPREAD_PCT = 0.0001      # a conservative round number for TSLA's typical tick-tight spread
ENTRY_SLIPPAGE_PCT = 0.0002   # market-order entry, normal conditions
STOP_EXIT_SLIPPAGE_PCT = 0.0005  # stop-triggered market exit, worse fills expected
# SEC Section 31 fee + FINRA Trading Activity Fee - small, mandatory,
# broker-independent regulatory fees on every SELL (never on buys),
# separate from Alpaca's own $0 commission. ~0.003% is a conservative
# round approximation of their combined recent-years magnitude.
SELL_REGULATORY_FEE_PCT = 0.00003


@dataclass
class BacktestConfig:
    initial_stop_pct: float
    trailing_activation_pct: float
    trailing_distance_pct: float

    @property
    def label(self) -> str:
        return (
            f"stop={self.initial_stop_pct:.0%}/"
            f"act={self.trailing_activation_pct:.0%}/"
            f"trail={self.trailing_distance_pct:.0%}"
        )


@dataclass
class Trade:
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    exit_reason: str  # "stopped_out" or "period_end"
    shares: float
    pnl: float
    pnl_pct: float


@dataclass
class BacktestResult:
    config: BacktestConfig
    period_start: str
    period_end: str
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)  # list of (date, equity)
    stop_history: list = field(default_factory=list)  # list of (date, stop_price) while in position
    days_in_position: int = 0
    total_days: int = 0
    total_buy_notional: float = 0.0
    total_sell_notional: float = 0.0
    final_equity: float = STARTING_CAPITAL


def _entry_fill_price(open_price: float) -> float:
    return open_price * (1 + HALF_SPREAD_PCT + ENTRY_SLIPPAGE_PCT)


def _stop_exit_fill_price(open_price: float, stop_price: float) -> float:
    triggered_price = open_price if open_price <= stop_price else stop_price
    return triggered_price * (1 - HALF_SPREAD_PCT - STOP_EXIT_SLIPPAGE_PCT - SELL_REGULATORY_FEE_PCT)


def _period_end_fill_price(close_price: float) -> float:
    # A planned liquidation, not a panic stop - normal (entry-level)
    # slippage applies, still with the mandatory sell-side regulatory fee.
    return close_price * (1 - HALF_SPREAD_PCT - ENTRY_SLIPPAGE_PCT - SELL_REGULATORY_FEE_PCT)


def run_backtest(bars: list, config: BacktestConfig) -> BacktestResult:
    if not bars:
        raise ValueError("no bars provided for backtest period")

    result = BacktestResult(
        config=config, period_start=bars[0]["date"], period_end=bars[-1]["date"]
    )

    cash = STARTING_CAPITAL
    shares = 0.0
    entry_price = None
    entry_date = None
    stop_price = None
    activated = False
    highest_since_activation = None
    in_position = False

    for i, bar in enumerate(bars):
        entered_today = False
        if not in_position:
            fill = _entry_fill_price(bar["open"])
            investable_cash = cash - COMMISSION
            shares = investable_cash / fill
            cash -= shares * fill + COMMISSION
            entry_price = fill
            entry_date = bar["date"]
            stop_price = entry_price * (1 - config.initial_stop_pct)
            activated = False
            highest_since_activation = None
            in_position = True
            entered_today = True
            result.total_buy_notional += shares * fill

        # A position bought at today's open is held for at least part of
        # today regardless of whether it's also exited later today - every
        # bar involves holding a position for some portion of the session,
        # since re-entry is immediate. Only the exit-check below can still
        # end the day flat.
        if not entered_today and bar["low"] <= stop_price:
            fill = _stop_exit_fill_price(bar["open"], stop_price)
            pnl = (fill - entry_price) * shares - COMMISSION
            cash += shares * fill - COMMISSION
            result.total_sell_notional += shares * fill
            result.trades.append(Trade(
                entry_date=entry_date, entry_price=entry_price,
                exit_date=bar["date"], exit_price=fill, exit_reason="stopped_out",
                shares=shares, pnl=pnl, pnl_pct=(fill / entry_price) - 1,
            ))
            shares = 0.0
            in_position = False
        elif not entered_today:
            activation_price = entry_price * (1 + config.trailing_activation_pct)
            if not activated and bar["high"] >= activation_price:
                activated = True
                highest_since_activation = bar["high"]
            elif activated:
                highest_since_activation = max(highest_since_activation, bar["high"])

            if activated:
                candidate_stop = highest_since_activation * (1 - config.trailing_distance_pct)
                stop_price = max(stop_price, candidate_stop)  # never move down

        # Both counters increment every single bar by construction: this
        # backtest's "always re-enter the instant we're flat" convention
        # means a position is held for at least part of every day, so
        # exposure = days_in_position / total_days is always exactly 1.0
        # for every configuration - a real, degenerate consequence of the
        # convention, not a bug, but not a differentiating metric either.
        result.days_in_position += 1
        result.stop_history.append((bar["date"], stop_price))

        mark_price = bar["close"]
        equity = cash + (shares * mark_price if in_position else 0.0)
        result.equity_curve.append((bar["date"], equity))
        result.total_days += 1

    if in_position:
        last_close = bars[-1]["close"]
        fill = _period_end_fill_price(last_close)
        pnl = (fill - entry_price) * shares - COMMISSION
        cash += shares * fill - COMMISSION
        result.total_sell_notional += shares * fill
        result.trades.append(Trade(
            entry_date=entry_date, entry_price=entry_price,
            exit_date=bars[-1]["date"], exit_price=fill, exit_reason="period_end",
            shares=shares, pnl=pnl, pnl_pct=(fill / entry_price) - 1,
        ))
        result.equity_curve[-1] = (bars[-1]["date"], cash)

    result.final_equity = cash
    return result
