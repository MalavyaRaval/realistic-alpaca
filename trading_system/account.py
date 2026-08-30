"""Account module: equity, cash, buying power, positions, and open orders.

Read-only. Every call is logged so there is a record of what the system
believed its own account state was at any point in time.
"""

from dataclasses import dataclass
from typing import Optional

from alpaca.trading.enums import OrderSide, QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from config import get_trading_client
from logging_setup import log_event

_trading_client = None


class AccountDataUnavailable(Exception):
    """Raised when account/position/order state can't be retrieved."""


def _trading():
    global _trading_client
    if _trading_client is None:
        _trading_client = get_trading_client()
    return _trading_client


@dataclass
class AccountSummary:
    equity: float
    cash: float
    buying_power: float
    last_equity: float
    status: str
    trading_blocked: bool
    account_blocked: bool
    pattern_day_trader: bool
    daytrade_count: int

    @property
    def daily_pl(self) -> float:
        return self.equity - self.last_equity


@dataclass
class PositionSummary:
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    unrealized_pl: float
    side: str


@dataclass
class OpenOrderSummary:
    id: str
    symbol: str
    side: str
    qty: float
    filled_qty: float
    order_type: str
    status: str
    submitted_at: object
    filled_avg_price: Optional[float] = None
    filled_at: object = None
    stop_price: Optional[float] = None
    trail_percent: Optional[float] = None
    hwm: Optional[float] = None


def get_account_summary() -> AccountSummary:
    try:
        a = _trading().get_account()
    except Exception as e:
        log_event("error", source="account.get_account_summary", message=str(e))
        raise AccountDataUnavailable(f"Could not retrieve account: {e}") from e

    summary = AccountSummary(
        equity=float(a.equity),
        cash=float(a.cash),
        buying_power=float(a.buying_power),
        last_equity=float(a.last_equity),
        status=a.status.value,
        trading_blocked=bool(a.trading_blocked),
        account_blocked=bool(a.account_blocked),
        pattern_day_trader=bool(a.pattern_day_trader),
        daytrade_count=int(a.daytrade_count or 0),
    )
    log_event(
        "market_data", action="account_summary", equity=summary.equity,
        cash=summary.cash, buying_power=summary.buying_power,
    )
    return summary


def get_positions() -> list:
    try:
        positions = _trading().get_all_positions()
    except Exception as e:
        log_event("error", source="account.get_positions", message=str(e))
        raise AccountDataUnavailable(f"Could not retrieve positions: {e}") from e

    result = [
        PositionSummary(
            symbol=p.symbol,
            qty=float(p.qty),
            avg_entry_price=float(p.avg_entry_price),
            market_value=float(p.market_value),
            unrealized_pl=float(p.unrealized_pl),
            side=p.side.value,
        )
        for p in positions
    ]
    log_event("market_data", action="positions", count=len(result))
    return result


def _to_order_summary(o) -> OpenOrderSummary:
    return OpenOrderSummary(
        id=str(o.id),
        symbol=o.symbol,
        side=o.side.value,
        qty=float(o.qty),
        filled_qty=float(o.filled_qty or 0),
        order_type=o.order_type.value,
        status=o.status.value,
        submitted_at=o.submitted_at,
        filled_avg_price=float(o.filled_avg_price) if o.filled_avg_price is not None else None,
        filled_at=o.filled_at,
        stop_price=float(o.stop_price) if o.stop_price is not None else None,
        trail_percent=float(o.trail_percent) if o.trail_percent is not None else None,
        hwm=float(o.hwm) if o.hwm is not None else None,
    )


def get_open_orders() -> list:
    try:
        orders = _trading().get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)
        )
    except Exception as e:
        log_event("error", source="account.get_open_orders", message=str(e))
        raise AccountDataUnavailable(f"Could not retrieve open orders: {e}") from e

    result = [_to_order_summary(o) for o in orders]
    log_event("market_data", action="open_orders", count=len(result))
    return result


def get_closed_orders(symbol: str, side: str = None, limit: int = 50) -> list:
    """Historical (filled/canceled/etc) orders for a symbol. Used to detect
    "we already completed a full cycle for this symbol" even after a
    protective order has filled and dropped out of the open-orders list -
    open-orders alone can't tell that apart from "never traded"."""
    kwargs = dict(status=QueryOrderStatus.CLOSED, symbols=[symbol], limit=limit)
    if side is not None:
        kwargs["side"] = OrderSide(side)

    try:
        orders = _trading().get_orders(filter=GetOrdersRequest(**kwargs))
    except Exception as e:
        log_event("error", source="account.get_closed_orders", symbol=symbol, message=str(e))
        raise AccountDataUnavailable(f"Could not retrieve closed orders for {symbol}: {e}") from e

    result = [_to_order_summary(o) for o in orders]
    log_event("market_data", action="closed_orders", symbol=symbol, count=len(result))
    return result
