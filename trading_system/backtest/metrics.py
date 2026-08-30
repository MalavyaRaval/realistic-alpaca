"""Metric definitions for a single BacktestResult.

Conventions, stated explicitly since these are all judgment calls:
- annualized_return uses calendar days / 365.25, compounded.
- max_drawdown is computed off the daily mark-to-market equity curve
  (includes unrealized moves while a position is open, not just realized
  trade P/L), and is reported as a negative fraction.
- turnover = (total dollar volume bought + sold) / average equity over the
  period - for a fully-reinvested single-symbol system this is close to
  2x the number of trades, but computed from actual cash flows.
- profit_factor and calmar_ratio both report None (not a number) when
  their denominator is zero (no losing trades; no drawdown at all),
  rather than a misleading and non-JSON-safe infinity.
"""

from datetime import date as date_cls
from statistics import mean

from backtest_engine import STARTING_CAPITAL


def compute_metrics(result) -> dict:
    trades = result.trades
    equity_curve = result.equity_curve

    total_return = (result.final_equity / STARTING_CAPITAL) - 1

    start_date = date_cls.fromisoformat(result.period_start)
    end_date = date_cls.fromisoformat(result.period_end)
    calendar_days = (end_date - start_date).days
    years = calendar_days / 365.25 if calendar_days > 0 else 0
    annualized_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0

    peak = float("-inf")
    max_dd = 0.0
    for _, equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            max_dd = min(max_dd, (equity / peak) - 1)

    n_trades = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]

    win_rate = len(wins) / n_trades if n_trades else 0.0
    avg_winning_trade_pct = mean([t.pnl_pct for t in wins]) if wins else 0.0
    avg_losing_trade_pct = mean([t.pnl_pct for t in losses]) if losses else 0.0

    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    largest_gain_pct = max((t.pnl_pct for t in trades), default=0.0)
    largest_loss_pct = min((t.pnl_pct for t in trades), default=0.0)

    exposure = result.days_in_position / result.total_days if result.total_days else 0.0
    avg_equity = mean(e for _, e in equity_curve) if equity_curve else STARTING_CAPITAL
    turnover = (
        (result.total_buy_notional + result.total_sell_notional) / avg_equity
        if avg_equity else 0.0
    )

    # None (not a number) when there's no drawdown to divide by at all -
    # matches profit_factor's convention and avoids a non-JSON-safe inf.
    calmar_ratio = annualized_return / abs(max_dd) if max_dd < 0 else None

    return {
        "config_label": result.config.label,
        "initial_stop_pct": result.config.initial_stop_pct,
        "trailing_activation_pct": result.config.trailing_activation_pct,
        "trailing_distance_pct": result.config.trailing_distance_pct,
        "period_start": result.period_start,
        "period_end": result.period_end,
        "total_return": total_return,
        "annualized_return": annualized_return,
        "max_drawdown": max_dd,
        "number_of_trades": n_trades,
        "win_rate": win_rate,
        "avg_winning_trade_pct": avg_winning_trade_pct,
        "avg_losing_trade_pct": avg_losing_trade_pct,
        "profit_factor": profit_factor,
        "largest_gain_pct": largest_gain_pct,
        "largest_loss_pct": largest_loss_pct,
        "exposure": exposure,
        "turnover": turnover,
        "calmar_ratio": calmar_ratio,
        "final_equity": result.final_equity,
    }
