"""Order-management module: submit, cancel, modify (where supported), verify
fills, and prevent duplicate orders.

This module never decides *whether* an order should happen - that's the
risk manager's job. By the time an OrderIntent reaches submit_order(), the
engine has already gotten a RiskDecision.approved == True. This module only
prevents the same order from going out twice and talks to the broker.
"""

import threading
import time
from dataclasses import dataclass
from typing import Optional

from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderSide, OrderStatus, TimeInForce
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    ReplaceOrderRequest,
    StopOrderRequest,
    TrailingStopOrderRequest,
)

from config import get_trading_client
from logging_setup import log_event
from strategy import OrderIntent

TERMINAL_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
    OrderStatus.DONE_FOR_DAY,
    OrderStatus.REPLACED,
}

# Guards against the same symbol+side being submitted twice in rapid
# succession, before the broker's open-orders list would reflect the first
# submission yet.
_DEBOUNCE_WINDOW_SECONDS = 5.0
_recent_submissions = {}
_debounce_lock = threading.Lock()

_trading_client = None


def _trading():
    global _trading_client
    if _trading_client is None:
        _trading_client = get_trading_client()
    return _trading_client


class OrderManagerError(Exception):
    """Raised on unexpected broker/API behavior - callers must fail closed
    (treat as 'did not happen', not retry blindly)."""


@dataclass
class OrderResult:
    accepted: bool
    order_id: Optional[str]
    status: Optional[str]
    symbol: str
    side: str
    qty: float
    filled_qty: float = 0.0
    filled_avg_price: Optional[float] = None
    reason: Optional[str] = None


def _open_orders_from_broker() -> list:
    """Authoritative duplicate check: ask the broker directly rather than
    trusting only a local cache, so duplicates are caught even across
    separate process runs."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    try:
        return _trading().get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
    except Exception as e:
        raise OrderManagerError(f"Could not check existing open orders for duplicates: {e}") from e


def _duplicate_reason(symbol: str, side: str) -> Optional[str]:
    key = (symbol, side)
    now = time.monotonic()
    with _debounce_lock:
        last = _recent_submissions.get(key)
        if last is not None and now - last < _DEBOUNCE_WINDOW_SECONDS:
            return (
                f"identical {side} order for {symbol} was submitted "
                f"{now - last:.1f}s ago (debounce window {_DEBOUNCE_WINDOW_SECONDS}s)"
            )
        # Reserve this slot now, under the lock and before the slow broker
        # round-trip below, so a second concurrent call for the same key
        # is blocked immediately by the check above instead of racing this
        # call to the broker's open-orders endpoint (both would otherwise
        # see "no open order yet" and both would submit).
        _recent_submissions[key] = now

    try:
        for o in _open_orders_from_broker():
            if o.symbol == symbol and o.side.value == side:
                return (
                    f"an open order already exists for {symbol} {side} "
                    f"(order id {o.id}, status {o.status.value})"
                )
    except OrderManagerError:
        # Couldn't actually verify anything - don't leave a phantom
        # reservation behind for a check that never completed.
        with _debounce_lock:
            if _recent_submissions.get(key) == now:
                del _recent_submissions[key]
        raise

    return None


def submit_order(intent: OrderIntent) -> OrderResult:
    dup_reason = _duplicate_reason(intent.symbol, intent.side)
    if dup_reason:
        log_event(
            "order", action="submit_blocked_duplicate", symbol=intent.symbol,
            side=intent.side, qty=intent.qty, reason=dup_reason,
        )
        return OrderResult(
            accepted=False, order_id=None, status="blocked_duplicate",
            symbol=intent.symbol, side=intent.side, qty=intent.qty, reason=dup_reason,
        )

    side_enum = OrderSide.BUY if intent.side == "buy" else OrderSide.SELL
    tif = TimeInForce(intent.time_in_force)

    if intent.order_type == "market":
        request = MarketOrderRequest(
            symbol=intent.symbol, qty=intent.qty, side=side_enum, time_in_force=tif
        )
    elif intent.order_type == "limit":
        if intent.limit_price is None:
            raise OrderManagerError("limit order requires limit_price")
        request = LimitOrderRequest(
            symbol=intent.symbol, qty=intent.qty, side=side_enum, time_in_force=tif,
            limit_price=intent.limit_price,
        )
    elif intent.order_type == "stop":
        if intent.stop_price is None:
            raise OrderManagerError("stop order requires stop_price")
        request = StopOrderRequest(
            symbol=intent.symbol, qty=intent.qty, side=side_enum, time_in_force=tif,
            stop_price=intent.stop_price,
        )
    elif intent.order_type == "trailing_stop":
        if intent.trail_percent is None:
            raise OrderManagerError("trailing_stop order requires trail_percent")
        request = TrailingStopOrderRequest(
            symbol=intent.symbol, qty=intent.qty, side=side_enum, time_in_force=tif,
            trail_percent=intent.trail_percent,
        )
    else:
        raise OrderManagerError(f"Unsupported order_type: {intent.order_type!r}")

    # Debounce slot for (symbol, side) was already reserved atomically
    # inside _duplicate_reason(), before the broker round-trip above - see
    # its comment for why the reservation has to happen there, not here.

    try:
        order = _trading().submit_order(request)
    except APIError as e:
        log_event(
            "order", action="submit_rejected_by_broker", symbol=intent.symbol,
            side=intent.side, qty=intent.qty, reason=str(e),
        )
        return OrderResult(
            accepted=False, order_id=None, status="broker_rejected",
            symbol=intent.symbol, side=intent.side, qty=intent.qty, reason=str(e),
        )
    except Exception as e:
        log_event(
            "error", source="order_manager.submit_order", symbol=intent.symbol,
            side=intent.side, qty=intent.qty, message=str(e),
        )
        raise OrderManagerError(f"Unexpected error submitting order for {intent.symbol}: {e}") from e

    log_event(
        "order", action="submitted", order_id=str(order.id), symbol=intent.symbol,
        side=intent.side, qty=intent.qty, status=order.status.value,
    )
    return OrderResult(
        accepted=True, order_id=str(order.id), status=order.status.value,
        symbol=intent.symbol, side=intent.side, qty=intent.qty,
    )


def cancel_order(order_id: str) -> bool:
    try:
        _trading().cancel_order_by_id(order_id)
    except APIError as e:
        log_event("cancellation", action="cancel_rejected", order_id=order_id, reason=str(e))
        return False
    except Exception as e:
        log_event("error", source="order_manager.cancel_order", order_id=order_id, message=str(e))
        raise OrderManagerError(f"Unexpected error cancelling order {order_id}: {e}") from e

    log_event("cancellation", action="cancelled", order_id=order_id)
    return True


def modify_order(
    order_id: str,
    qty: Optional[float] = None,
    limit_price: Optional[float] = None,
    time_in_force: Optional[str] = None,
) -> OrderResult:
    """Modify a still-open order where the broker supports it (e.g. limit
    orders that haven't filled). Market orders and terminal orders generally
    can't be replaced - that comes back as a broker rejection, not a crash."""
    updates = {}
    if qty is not None:
        updates["qty"] = qty
    if limit_price is not None:
        updates["limit_price"] = limit_price
    if time_in_force is not None:
        updates["time_in_force"] = TimeInForce(time_in_force)

    if not updates:
        raise OrderManagerError("modify_order called with no fields to update")

    try:
        order = _trading().replace_order_by_id(order_id, ReplaceOrderRequest(**updates))
    except APIError as e:
        log_event("order", action="modify_rejected", order_id=order_id, reason=str(e))
        return OrderResult(
            accepted=False, order_id=order_id, status="modify_rejected",
            symbol="", side="", qty=qty or 0.0, reason=str(e),
        )
    except Exception as e:
        log_event("error", source="order_manager.modify_order", order_id=order_id, message=str(e))
        raise OrderManagerError(f"Unexpected error modifying order {order_id}: {e}") from e

    log_event(
        "order", action="modified", order_id=str(order.id), new_status=order.status.value
    )
    return OrderResult(
        accepted=True, order_id=str(order.id), status=order.status.value,
        symbol=order.symbol, side=order.side.value, qty=float(order.qty),
    )


def verify_fill(order_id: str, timeout: float = 30.0, poll_interval: float = 2.0) -> OrderResult:
    """Polls an order until it reaches a terminal status or the timeout
    elapses. Returns whatever the last known state was - never guesses."""
    deadline = time.monotonic() + timeout

    while True:
        try:
            order = _trading().get_order_by_id(order_id)
        except Exception as e:
            log_event("error", source="order_manager.verify_fill", order_id=order_id, message=str(e))
            raise OrderManagerError(f"Unexpected error checking order {order_id}: {e}") from e

        if order.status == OrderStatus.FILLED:
            log_event(
                "fill", order_id=order_id, symbol=order.symbol,
                qty=float(order.filled_qty),
                fill_price=float(order.filled_avg_price) if order.filled_avg_price else None,
                filled_at=order.filled_at,
            )
            return OrderResult(
                accepted=True, order_id=order_id, status=order.status.value,
                symbol=order.symbol, side=order.side.value, qty=float(order.qty),
                filled_qty=float(order.filled_qty),
                filled_avg_price=float(order.filled_avg_price) if order.filled_avg_price else None,
            )

        if order.status in TERMINAL_STATUSES:
            log_event("order", action="terminal_no_fill", order_id=order_id, status=order.status.value)
            return OrderResult(
                accepted=False, order_id=order_id, status=order.status.value,
                symbol=order.symbol, side=order.side.value, qty=float(order.qty),
                reason=f"order ended in status {order.status.value} without filling",
            )

        if time.monotonic() >= deadline:
            log_event("order", action="verify_fill_timeout", order_id=order_id, last_status=order.status.value)
            return OrderResult(
                accepted=False, order_id=order_id, status=order.status.value,
                symbol=order.symbol, side=order.side.value, qty=float(order.qty),
                reason="verify_fill timed out before reaching a terminal status",
            )

        time.sleep(poll_interval)
