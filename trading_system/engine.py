"""Execution engine: wires market data, account state, risk management, and
order management together.

Contains no strategy logic. It receives a Signal and (for BUY/SELL) an
OrderIntent produced by whatever strategy is running, and enforces that the
risk manager approves every intent before order_manager ever sees it. This
is the only path into order_manager.submit_order that the rest of the
framework is meant to use.
"""

from dataclasses import dataclass
from typing import Optional

import account
import market_data
import order_manager
from logging_setup import log_event
from risk_manager import RiskManager
from strategy import OrderIntent, Signal


@dataclass
class ExecutionResult:
    signal: Signal
    approved: Optional[bool]  # None when no order was ever considered (HOLD/NO_ACTION)
    order_result: Optional[order_manager.OrderResult]
    reason: str


def process_signal(
    strategy_name: str,
    signal: Signal,
    intent: Optional[OrderIntent],
    risk_mgr: RiskManager,
) -> ExecutionResult:
    log_event(
        "strategy",
        strategy=strategy_name,
        signal=signal.value,
        symbol=intent.symbol if intent else None,
        side=intent.side if intent else None,
        qty=intent.qty if intent else None,
    )

    if signal in (Signal.HOLD, Signal.NO_ACTION):
        return ExecutionResult(
            signal=signal, approved=None, order_result=None,
            reason=f"{signal.value}: no order generated",
        )

    if intent is None:
        raise ValueError(f"signal {signal.value} requires an OrderIntent")

    # Fail closed: any of these being unavailable blocks the order rather
    # than proceeding on stale/guessed data.
    try:
        market_data.is_market_open()
    except market_data.MarketDataUnavailable as e:
        log_event("error", source="engine.process_signal", message=f"market clock unavailable: {e}")
        return ExecutionResult(
            signal=signal, approved=False, order_result=None,
            reason=f"blocked: market status unavailable ({e})",
        )

    try:
        current_price = market_data.get_current_price(intent.symbol)
    except market_data.MarketDataUnavailable as e:
        log_event("error", source="engine.process_signal", message=f"price unavailable: {e}")
        return ExecutionResult(
            signal=signal, approved=False, order_result=None,
            reason=f"blocked: price unavailable ({e})",
        )

    try:
        acct = account.get_account_summary()
        positions = account.get_positions()
    except account.AccountDataUnavailable as e:
        log_event("error", source="engine.process_signal", message=f"account data unavailable: {e}")
        return ExecutionResult(
            signal=signal, approved=False, order_result=None,
            reason=f"blocked: account data unavailable ({e})",
        )

    decision = risk_mgr.evaluate(intent, current_price, acct, positions)
    if not decision.approved:
        return ExecutionResult(
            signal=signal, approved=False, order_result=None, reason=decision.reason
        )

    try:
        result = order_manager.submit_order(intent)
    except order_manager.OrderManagerError as e:
        log_event("error", source="engine.process_signal", message=f"order submission failed: {e}")
        return ExecutionResult(
            signal=signal, approved=True, order_result=None,
            reason=f"risk-approved but submission failed: {e}",
        )

    return ExecutionResult(
        signal=signal, approved=True, order_result=result,
        reason=result.reason or f"submitted, status={result.status}",
    )
