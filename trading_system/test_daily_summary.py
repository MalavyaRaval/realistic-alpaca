"""Pure logic tests for daily_summary - account/log reads are monkeypatched,
so nothing here touches the live account or the real event log file."""

import account
import daily_summary as daily_summary_module
from account import OpenOrderSummary, PositionSummary
from daily_summary import build_daily_summary

DATE = "2026-08-31"


def order(id, side, status, order_type="market", submitted_at=f"{DATE}T14:30:00+00:00",
          filled_at=None, filled_qty=0, filled_avg_price=None, stop_price=None,
          trail_percent=None, hwm=None, qty=10):
    return OpenOrderSummary(
        id=id, symbol="TSLA", side=side, qty=qty, filled_qty=filled_qty,
        order_type=order_type, status=status, submitted_at=submitted_at,
        filled_at=filled_at, filled_avg_price=filled_avg_price,
        stop_price=stop_price, trail_percent=trail_percent, hwm=hwm,
    )


def test_realized_and_unrealized_pl_after_a_round_trip(monkeypatch):
    entry_fill = order(
        "entry-1", "buy", "filled", filled_qty=10, filled_avg_price=300.0,
        filled_at=f"{DATE}T14:31:00+00:00",
    )
    exit_fill = order(
        "exit-1", "sell", "filled", order_type="stop", filled_qty=10,
        filled_avg_price=330.0, filled_at=f"{DATE}T16:00:00+00:00",
    )
    monkeypatch.setattr(account, "get_closed_orders", lambda symbol, limit=200: [entry_fill, exit_fill])
    monkeypatch.setattr(account, "get_open_orders", lambda: [])
    monkeypatch.setattr(account, "get_positions", lambda: [])

    summary = build_daily_summary("TSLA", date_str=DATE)
    assert len(summary.fills) == 2
    assert summary.realized_pl == (330.0 - 300.0) * 10
    assert summary.unrealized_pl == 0.0


def test_unrealized_pl_from_open_position(monkeypatch):
    monkeypatch.setattr(account, "get_closed_orders", lambda symbol, limit=200: [])
    open_stop = order("stop-1", "sell", "accepted", order_type="stop", stop_price=270.0)
    monkeypatch.setattr(account, "get_open_orders", lambda: [open_stop])
    position = PositionSummary(symbol="TSLA", qty=10, avg_entry_price=300.0, market_value=3050, unrealized_pl=50.0, side="long")
    monkeypatch.setattr(account, "get_positions", lambda: [position])

    summary = build_daily_summary("TSLA", date_str=DATE)
    assert summary.realized_pl == 0.0
    assert summary.unrealized_pl == 50.0
    assert len(summary.stop_levels) == 1
    assert summary.stop_levels[0]["stop_price"] == 270.0


def test_orders_outside_the_target_date_are_excluded(monkeypatch):
    yesterday_order = order("old-1", "buy", "filled", submitted_at="2026-08-30T14:00:00+00:00", filled_qty=10, filled_avg_price=290.0, filled_at="2026-08-30T14:01:00+00:00")
    todays_order = order("today-1", "buy", "filled", filled_qty=10, filled_avg_price=300.0, filled_at=f"{DATE}T14:31:00+00:00")
    monkeypatch.setattr(account, "get_closed_orders", lambda symbol, limit=200: [yesterday_order, todays_order])
    monkeypatch.setattr(account, "get_open_orders", lambda: [])
    monkeypatch.setattr(account, "get_positions", lambda: [])

    summary = build_daily_summary("TSLA", date_str=DATE)
    assert len(summary.trades) == 1
    assert summary.trades[0].id == "today-1"


def test_errors_and_risk_events_filtered_to_date(monkeypatch):
    events = [
        {"timestamp": f"{DATE}T15:00:00+00:00", "category": "error", "source": "x", "message": "boom"},
        {"timestamp": "2026-08-30T15:00:00+00:00", "category": "error", "source": "x", "message": "old boom"},
        {"timestamp": f"{DATE}T15:05:00+00:00", "category": "risk_rejection", "reason": "too big"},
        {"timestamp": f"{DATE}T15:06:00+00:00", "category": "risk_approval", "reason": "ok"},
    ]
    monkeypatch.setattr(
        daily_summary_module, "read_events",
        lambda category=None: [e for e in events if category is None or e["category"] == category],
    )
    monkeypatch.setattr(account, "get_closed_orders", lambda symbol, limit=200: [])
    monkeypatch.setattr(account, "get_open_orders", lambda: [])
    monkeypatch.setattr(account, "get_positions", lambda: [])

    summary = build_daily_summary("TSLA", date_str=DATE)
    assert len(summary.errors) == 1
    assert summary.errors[0]["message"] == "boom"
    assert len(summary.risk_events) == 2
