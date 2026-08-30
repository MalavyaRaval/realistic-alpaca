"""Daily summary for a symbol being traded by the monitoring job.

Trades/fills/positions/stop-levels come from Alpaca directly (broker
ground truth); errors/risk events come from our own event log, since
Alpaca has no record of a rejection our own risk manager made before ever
reaching the broker.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

import account
from logging_setup import read_events


@dataclass
class DailySummary:
    date: str
    symbol: str
    trades: list = field(default_factory=list)
    fills: list = field(default_factory=list)
    realized_pl: float = 0.0
    unrealized_pl: float = 0.0
    positions: list = field(default_factory=list)
    stop_levels: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    risk_events: list = field(default_factory=list)


def _on_date(timestamp, date_str: str) -> bool:
    return timestamp is not None and str(timestamp).startswith(date_str)


def _realized_pl(fills: list) -> float:
    buys = [o for o in fills if o.side == "buy" and o.filled_avg_price is not None]
    sells = [o for o in fills if o.side == "sell" and o.filled_avg_price is not None]
    if not buys or not sells:
        return 0.0

    total_buy_qty = sum(o.filled_qty for o in buys)
    if total_buy_qty == 0:
        return 0.0
    avg_buy_price = sum(o.filled_avg_price * o.filled_qty for o in buys) / total_buy_qty

    return sum((o.filled_avg_price - avg_buy_price) * o.filled_qty for o in sells)


def build_daily_summary(symbol: str, date_str: str = None) -> DailySummary:
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    closed_orders = account.get_closed_orders(symbol, limit=200)
    open_orders = [o for o in account.get_open_orders() if o.symbol == symbol]

    todays_orders = [
        o for o in (closed_orders + open_orders)
        if _on_date(o.submitted_at, date_str)
    ]
    fills = [o for o in todays_orders if o.status == "filled" and _on_date(o.filled_at, date_str)]

    positions = [p for p in account.get_positions() if p.symbol == symbol]
    unrealized_pl = sum(p.unrealized_pl for p in positions)

    protective_orders = [
        o for o in open_orders if o.side == "sell" and o.order_type in ("stop", "trailing_stop")
    ]
    stop_levels = [
        {
            "order_id": o.id, "order_type": o.order_type, "stop_price": o.stop_price,
            "trail_percent": o.trail_percent, "hwm": o.hwm,
        }
        for o in protective_orders
    ]

    errors = [e for e in read_events("error") if _on_date(e.get("timestamp"), date_str)]
    risk_events = [
        e for e in read_events("risk_rejection") + read_events("risk_approval")
        if _on_date(e.get("timestamp"), date_str)
    ]
    risk_events.sort(key=lambda e: e.get("timestamp", ""))

    return DailySummary(
        date=date_str,
        symbol=symbol,
        trades=todays_orders,
        fills=fills,
        realized_pl=_realized_pl(fills),
        unrealized_pl=unrealized_pl,
        positions=positions,
        stop_levels=stop_levels,
        errors=errors,
        risk_events=risk_events,
    )


def format_summary(summary: DailySummary) -> str:
    lines = [
        f"=== Daily Summary: {summary.symbol} on {summary.date} ===",
        f"Trades submitted: {len(summary.trades)}",
    ]
    for o in summary.trades:
        lines.append(f"  {o.side} {o.qty} {o.symbol} ({o.order_type}) - status={o.status}")

    lines.append(f"Fills: {len(summary.fills)}")
    for o in summary.fills:
        lines.append(f"  {o.side} {o.filled_qty} @ {o.filled_avg_price} (order {o.id})")

    lines.append(f"Realized P/L: {summary.realized_pl:.2f}")
    lines.append(f"Unrealized P/L: {summary.unrealized_pl:.2f}")

    lines.append(f"Current positions: {len(summary.positions)}")
    for p in summary.positions:
        lines.append(f"  {p.symbol}: qty={p.qty}, avg_entry={p.avg_entry_price}, unrealized_pl={p.unrealized_pl}")

    lines.append(f"Current stop levels: {len(summary.stop_levels)}")
    for s in summary.stop_levels:
        lines.append(f"  {s['order_type']} stop_price={s['stop_price']} trail_percent={s['trail_percent']} hwm={s['hwm']}")

    lines.append(f"Errors: {len(summary.errors)}")
    for e in summary.errors:
        lines.append(f"  [{e.get('timestamp')}] {e.get('source')}: {e.get('message')}")

    lines.append(f"Risk events: {len(summary.risk_events)}")
    for e in summary.risk_events:
        lines.append(f"  [{e.get('timestamp')}] {e.get('category')}: {e.get('reason', e.get('checks'))}")

    return "\n".join(lines)
