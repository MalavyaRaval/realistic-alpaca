"""Pure logic tests for TrailingStopStrategy - every dependency (market
data, account state, order_manager, engine) is monkeypatched, so nothing
here touches the live account."""

import account
import engine
import market_data
import order_manager
from account import OpenOrderSummary, PositionSummary
from engine import ExecutionResult
from order_manager import OrderResult
from risk_manager import RiskLimits, RiskManager
from strategy import Signal
from trailing_stop_strategy import TrailingStopParams, TrailingStopStrategy

RISK = RiskManager(RiskLimits(
    max_position_size=1000, max_dollar_exposure=50_000, max_portfolio_exposure=50_000,
    max_daily_loss=1_000_000, max_simultaneous_positions=50, max_order_size=50_000,
))


def make_strategy(**overrides):
    params = TrailingStopParams(symbol="TSLA", qty=10, **overrides)
    return TrailingStopStrategy(params)


def test_enters_when_flat_no_orders_no_history(monkeypatch):
    monkeypatch.setattr(account, "get_positions", lambda: [])
    monkeypatch.setattr(account, "get_open_orders", lambda: [])
    monkeypatch.setattr(account, "get_closed_orders", lambda symbol, side=None, limit=50: [])
    monkeypatch.setattr(market_data, "get_current_price", lambda symbol: 300.0)
    monkeypatch.setattr(market_data, "get_volume", lambda symbol: 1_000_000)
    monkeypatch.setattr(market_data, "is_market_open", lambda: False)

    submitted = OrderResult(accepted=True, order_id="entry-1", status="accepted", symbol="TSLA", side="buy", qty=10)
    monkeypatch.setattr(
        engine, "process_signal",
        lambda name, signal, intent, risk_mgr: ExecutionResult(signal=signal, approved=True, order_result=submitted, reason="submitted"),
    )

    result = make_strategy().run_cycle(RISK)
    assert result.action == "entry_submitted"
    assert result.order_result.order_id == "entry-1"


def test_waits_when_entry_order_still_pending(monkeypatch):
    monkeypatch.setattr(account, "get_positions", lambda: [])
    pending = OpenOrderSummary(id="pending-1", symbol="TSLA", side="buy", qty=10, filled_qty=0, order_type="market", status="accepted", submitted_at=None)
    monkeypatch.setattr(account, "get_open_orders", lambda: [pending])

    def fail_if_called(*a, **k):
        raise AssertionError("must not submit a second entry while one is still pending")
    monkeypatch.setattr(engine, "process_signal", fail_if_called)

    result = make_strategy().run_cycle(RISK)
    assert result.action == "waiting"
    assert "pending-1" in result.detail


def test_detects_already_completed_cycle_and_does_not_reenter(monkeypatch):
    monkeypatch.setattr(account, "get_positions", lambda: [])
    monkeypatch.setattr(account, "get_open_orders", lambda: [])
    filled_entry = OpenOrderSummary(id="old-entry", symbol="TSLA", side="buy", qty=10, filled_qty=10, order_type="market", status="filled", submitted_at=None)
    monkeypatch.setattr(account, "get_closed_orders", lambda symbol, side=None, limit=50: [filled_entry])

    def fail_if_called(*a, **k):
        raise AssertionError("must not re-enter after a previous cycle already completed")
    monkeypatch.setattr(engine, "process_signal", fail_if_called)

    strategy = make_strategy()
    result = strategy.run_cycle(RISK)
    assert result.action == "exited"
    assert strategy._done is True

    # Second call should hit the fast in-memory path, not re-check history.
    monkeypatch.setattr(account, "get_closed_orders", fail_if_called)
    result2 = strategy.run_cycle(RISK)
    assert result2.action == "no_action"


def test_places_initial_stop_from_actual_fill_price(monkeypatch):
    position = PositionSummary(symbol="TSLA", qty=10, avg_entry_price=300.0, market_value=3000, unrealized_pl=0, side="long")
    monkeypatch.setattr(account, "get_positions", lambda: [position])
    monkeypatch.setattr(account, "get_open_orders", lambda: [])
    monkeypatch.setattr(market_data, "get_current_price", lambda symbol: 305.0)  # not activated yet

    captured = {}
    def fake_submit(intent):
        captured["intent"] = intent
        return OrderResult(accepted=True, order_id="stop-1", status="accepted", symbol=intent.symbol, side=intent.side, qty=intent.qty)
    monkeypatch.setattr(order_manager, "submit_order", fake_submit)

    result = make_strategy(initial_stop_pct=0.10).run_cycle(RISK)
    assert result.action == "initial_stop_placed"
    assert captured["intent"].order_type == "stop"
    assert captured["intent"].stop_price == 270.0  # 300 * 0.9, from actual fill price
    assert captured["intent"].time_in_force == "gtc"


def test_upgrades_to_trailing_stop_once_activated(monkeypatch):
    position = PositionSummary(symbol="TSLA", qty=10, avg_entry_price=300.0, market_value=3630, unrealized_pl=330, side="long")
    fixed_stop = OpenOrderSummary(id="fixed-1", symbol="TSLA", side="sell", qty=10, filled_qty=0, order_type="stop", status="accepted", submitted_at=None)
    monkeypatch.setattr(account, "get_positions", lambda: [position])
    monkeypatch.setattr(account, "get_open_orders", lambda: [fixed_stop])
    monkeypatch.setattr(market_data, "get_current_price", lambda symbol: 331.0)  # >= 300 * 1.10 activation

    cancelled = {}
    monkeypatch.setattr(order_manager, "cancel_order", lambda order_id: cancelled.setdefault("id", order_id) or True)

    captured = {}
    def fake_submit(intent):
        captured["intent"] = intent
        return OrderResult(accepted=True, order_id="trailing-1", status="accepted", symbol=intent.symbol, side=intent.side, qty=intent.qty)
    monkeypatch.setattr(order_manager, "submit_order", fake_submit)

    result = make_strategy(trailing_activation_pct=0.10, trailing_distance_pct=0.05).run_cycle(RISK)
    assert cancelled["id"] == "fixed-1"
    assert result.action == "trailing_stop_activated"
    assert captured["intent"].order_type == "trailing_stop"
    assert captured["intent"].trail_percent == 5.0
    assert captured["intent"].time_in_force == "gtc"


def test_does_nothing_when_trailing_stop_already_active(monkeypatch):
    position = PositionSummary(symbol="TSLA", qty=10, avg_entry_price=300.0, market_value=3630, unrealized_pl=330, side="long")
    trailing = OpenOrderSummary(id="trailing-1", symbol="TSLA", side="sell", qty=10, filled_qty=0, order_type="trailing_stop", status="accepted", submitted_at=None)
    monkeypatch.setattr(account, "get_positions", lambda: [position])
    monkeypatch.setattr(account, "get_open_orders", lambda: [trailing])
    monkeypatch.setattr(market_data, "get_current_price", lambda symbol: 340.0)

    def fail_if_called(*a, **k):
        raise AssertionError("must never recompute or replace an existing trailing stop")
    monkeypatch.setattr(order_manager, "cancel_order", fail_if_called)
    monkeypatch.setattr(order_manager, "submit_order", fail_if_called)

    result = make_strategy().run_cycle(RISK)
    assert result.action == "holding"
    assert "trailing-1" in result.detail


def test_holds_with_fixed_stop_when_not_yet_activated(monkeypatch):
    position = PositionSummary(symbol="TSLA", qty=10, avg_entry_price=300.0, market_value=3050, unrealized_pl=50, side="long")
    fixed_stop = OpenOrderSummary(id="fixed-1", symbol="TSLA", side="sell", qty=10, filled_qty=0, order_type="stop", status="accepted", submitted_at=None)
    monkeypatch.setattr(account, "get_positions", lambda: [position])
    monkeypatch.setattr(account, "get_open_orders", lambda: [fixed_stop])
    monkeypatch.setattr(market_data, "get_current_price", lambda symbol: 305.0)  # below 330 activation

    def fail_if_called(*a, **k):
        raise AssertionError("must not touch orders before activation threshold is reached")
    monkeypatch.setattr(order_manager, "cancel_order", fail_if_called)
    monkeypatch.setattr(order_manager, "submit_order", fail_if_called)

    result = make_strategy(trailing_activation_pct=0.10).run_cycle(RISK)
    assert result.action == "holding"
    assert "fixed-1" in result.detail
