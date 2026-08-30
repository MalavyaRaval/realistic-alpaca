"""Monitoring job for a running TrailingStopStrategy.

Meant to be invoked once per call, every 5 minutes, by an external
scheduler - it is safe to call at any time (market open or closed) and does
nothing destructive on its own. Each invocation:

  1. Checks whether this symbol is halted (persisted in monitor_state.json)
     - if so, does nothing and logs that it skipped.
  2. Reconciles the actual live position, open orders, and current price
     from Alpaca (never trusts memory from a prior invocation).
  3. Runs defensive anomaly checks (more than one protective order resting,
     a stop that appears to have moved down, a trailing stop that doesn't
     match its own hwm/trail_percent math) - any of these halts immediately.
  4. Outside market hours: logs the reconciliation and stops there. No
     trading, no order modification - "safe reconciliation" here means
     read-only observation, never an automatic fix; a detected anomaly
     halts rather than being auto-corrected, even outside market hours.
  5. During market hours, if nothing is wrong: hands off to
     TrailingStopStrategy.run_cycle(), which is the only thing that can
     actually enter, place a stop, or upgrade to trailing.

Any exception anywhere in this sequence - expected or not - is treated as
"anything unexpected happened": it is logged and the symbol is halted so
every subsequent invocation is a no-op until a person investigates and
calls monitor_state.clear_halt(symbol). This module never tries to
improvise a fix.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import account
import market_data
import monitor_state
from logging_setup import log_event
from risk_manager import RiskManager
from trailing_stop_strategy import TrailingStopStrategy

STOP_TOLERANCE = 0.01  # dollars; broker-vs-recomputed trailing stop mismatch beyond this halts


class MonitorAnomaly(Exception):
    """An internal defensive check tripped - something is inconsistent
    enough that the monitor should not proceed on its own."""


@dataclass
class MonitorRecord:
    timestamp: str
    symbol: str
    current_price: Optional[float]
    avg_entry: Optional[float]
    highest_price_observed: Optional[float]
    stop_level: Optional[float]
    position_qty: float
    open_order_ids: list = field(default_factory=list)
    action: str = ""
    reason: str = ""


def _log(record: MonitorRecord) -> MonitorRecord:
    log_event(
        "monitor",
        timestamp=record.timestamp,
        symbol=record.symbol,
        current_price=record.current_price,
        avg_entry=record.avg_entry,
        highest_price_observed=record.highest_price_observed,
        stop_level=record.stop_level,
        position_qty=record.position_qty,
        open_order_ids=record.open_order_ids,
        action=record.action,
        reason=record.reason,
    )
    return record


def run_monitoring_cycle(strategy: TrailingStopStrategy, risk_mgr: RiskManager) -> MonitorRecord:
    symbol = strategy.params.symbol
    timestamp = datetime.now(timezone.utc).isoformat()
    state = monitor_state.load_state()
    sym_state = monitor_state.get_symbol_state(state, symbol)

    if sym_state["halted"]:
        return _log(MonitorRecord(
            timestamp=timestamp, symbol=symbol, current_price=None, avg_entry=None,
            highest_price_observed=sym_state["highest_price_observed"],
            stop_level=sym_state["last_stop_level"], position_qty=0,
            action="halted_skip", reason=f"monitor is halted: {sym_state['halt_reason']}",
        ))

    try:
        market_open = market_data.is_market_open()
        positions = account.get_positions()
        open_orders = account.get_open_orders()
        current_price = market_data.get_current_price(symbol)

        position = next((p for p in positions if p.symbol == symbol), None)
        symbol_orders = [o for o in open_orders if o.symbol == symbol]
        protective_orders = [
            o for o in symbol_orders
            if o.side == "sell" and o.order_type in ("stop", "trailing_stop")
        ]

        if len(protective_orders) > 1:
            raise MonitorAnomaly(
                f"found {len(protective_orders)} protective orders open simultaneously "
                f"for {symbol}: {[o.id for o in protective_orders]} - should never be more than one"
            )

        avg_entry = position.avg_entry_price if position else None
        position_qty = position.qty if position else 0.0

        protective_order = protective_orders[0] if protective_orders else None
        stop_level = protective_order.stop_price if protective_order else None
        broker_hwm = protective_order.hwm if protective_order else None

        prior_highest = sym_state["highest_price_observed"]
        candidates = [v for v in (prior_highest, current_price, broker_hwm) if v is not None]
        highest_price_observed = max(candidates) if candidates else None

        prior_stop = sym_state["last_stop_level"]
        if prior_stop is not None and stop_level is not None and stop_level < prior_stop - 1e-9:
            raise MonitorAnomaly(
                f"stop level for {symbol} decreased from {prior_stop} to {stop_level} - "
                f"a stop must never move down"
            )

        if protective_order and protective_order.order_type == "trailing_stop" and broker_hwm and protective_order.trail_percent:
            expected_stop = broker_hwm * (1 - protective_order.trail_percent / 100)
            if stop_level is not None and abs(stop_level - expected_stop) > STOP_TOLERANCE:
                raise MonitorAnomaly(
                    f"trailing stop for {symbol} reports stop_price={stop_level} but "
                    f"hwm={broker_hwm} and trail_percent={protective_order.trail_percent} "
                    f"imply {expected_stop:.4f} - mismatch exceeds tolerance {STOP_TOLERANCE}"
                )

        monitor_state.update_symbol_state(
            state, symbol,
            highest_price_observed=highest_price_observed,
            last_stop_level=stop_level,
            last_run_at=timestamp,
        )

        if not market_open:
            monitor_state.save_state(state)
            return _log(MonitorRecord(
                timestamp=timestamp, symbol=symbol, current_price=current_price,
                avg_entry=avg_entry, highest_price_observed=highest_price_observed,
                stop_level=stop_level, position_qty=position_qty,
                open_order_ids=[o.id for o in symbol_orders],
                action="no_action", reason="market closed; reconciliation only, no trading",
            ))

        cycle_result = strategy.run_cycle(risk_mgr)
        monitor_state.save_state(state)
        return _log(MonitorRecord(
            timestamp=timestamp, symbol=symbol, current_price=current_price,
            avg_entry=avg_entry, highest_price_observed=highest_price_observed,
            stop_level=stop_level, position_qty=position_qty,
            open_order_ids=[o.id for o in symbol_orders],
            action=cycle_result.action, reason=cycle_result.detail,
        ))

    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        monitor_state.set_halted(state, symbol, reason)
        monitor_state.save_state(state)
        log_event("error", source="monitor.run_monitoring_cycle", symbol=symbol, message=reason)
        return _log(MonitorRecord(
            timestamp=timestamp, symbol=symbol, current_price=None, avg_entry=None,
            highest_price_observed=sym_state["highest_price_observed"],
            stop_level=sym_state["last_stop_level"], position_qty=0,
            action="halted", reason=f"unexpected condition, halting: {reason}",
        ))
