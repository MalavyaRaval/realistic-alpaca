"""Pure logic tests for the monitoring job - every dependency (market data,
account state, strategy, persisted state) is monkeypatched, so nothing here
touches the live account or the real monitor_state.json file."""

import account
import market_data
import monitor
import monitor_state
from account import OpenOrderSummary, PositionSummary
from monitor import run_monitoring_cycle
from risk_manager import RiskLimits, RiskManager
from trailing_stop_strategy import CycleResult, TrailingStopParams, TrailingStopStrategy

RISK = RiskManager(RiskLimits(
    max_position_size=1000, max_dollar_exposure=50_000, max_portfolio_exposure=50_000,
    max_daily_loss=1_000_000, max_simultaneous_positions=50, max_order_size=50_000,
))


def make_strategy(**overrides):
    return TrailingStopStrategy(TrailingStopParams(symbol="TSLA", qty=10, **overrides))


def use_fake_state(monkeypatch, initial: dict = None):
    """Backs monitor_state with a plain in-memory dict for the duration of
    a test, so persistence can be observed without touching real files."""
    store = {"data": initial or {}}
    monkeypatch.setattr(monitor_state, "load_state", lambda: store["data"])
    monkeypatch.setattr(monitor_state, "save_state", lambda state: store.update(data=state))
    return store


def fail_if_called(*a, **k):
    raise AssertionError("must not be called")


def test_halted_symbol_skips_everything(monkeypatch):
    use_fake_state(monkeypatch, {"TSLA": {"halted": True, "halt_reason": "manual test halt", "highest_price_observed": 350.0, "last_stop_level": 315.0, "last_run_at": None}})
    monkeypatch.setattr(market_data, "is_market_open", fail_if_called)
    monkeypatch.setattr(account, "get_positions", fail_if_called)
    monkeypatch.setattr(account, "get_open_orders", fail_if_called)

    result = run_monitoring_cycle(make_strategy(), RISK)
    assert result.action == "halted_skip"
    assert "manual test halt" in result.reason
    assert result.stop_level == 315.0


def test_outside_market_hours_reconciles_but_does_not_trade(monkeypatch):
    store = use_fake_state(monkeypatch)
    monkeypatch.setattr(market_data, "is_market_open", lambda: False)
    position = PositionSummary(symbol="TSLA", qty=10, avg_entry_price=300.0, market_value=3050, unrealized_pl=50, side="long")
    monkeypatch.setattr(account, "get_positions", lambda: [position])
    stop_order = OpenOrderSummary(id="stop-1", symbol="TSLA", side="sell", qty=10, filled_qty=0, order_type="stop", status="accepted", submitted_at=None, stop_price=270.0)
    monkeypatch.setattr(account, "get_open_orders", lambda: [stop_order])
    monkeypatch.setattr(market_data, "get_current_price", lambda symbol: 305.0)

    import trailing_stop_strategy
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", fail_if_called)

    result = run_monitoring_cycle(make_strategy(), RISK)
    assert result.action == "no_action"
    assert "market closed" in result.reason
    assert result.stop_level == 270.0
    assert store["data"]["TSLA"]["last_stop_level"] == 270.0


def test_normal_cycle_calls_strategy_and_logs_full_record(monkeypatch):
    use_fake_state(monkeypatch)
    monkeypatch.setattr(market_data, "is_market_open", lambda: True)
    position = PositionSummary(symbol="TSLA", qty=10, avg_entry_price=300.0, market_value=3050, unrealized_pl=50, side="long")
    monkeypatch.setattr(account, "get_positions", lambda: [position])
    stop_order = OpenOrderSummary(id="stop-1", symbol="TSLA", side="sell", qty=10, filled_qty=0, order_type="stop", status="accepted", submitted_at=None, stop_price=270.0)
    monkeypatch.setattr(account, "get_open_orders", lambda: [stop_order])
    monkeypatch.setattr(market_data, "get_current_price", lambda symbol: 305.0)

    import trailing_stop_strategy
    monkeypatch.setattr(
        trailing_stop_strategy.TrailingStopStrategy, "run_cycle",
        lambda self, risk_mgr: CycleResult("holding", "fixed stop still active"),
    )

    result = run_monitoring_cycle(make_strategy(), RISK)
    assert result.action == "holding"
    assert result.symbol == "TSLA"
    assert result.current_price == 305.0
    assert result.avg_entry == 300.0
    assert result.position_qty == 10
    assert result.stop_level == 270.0
    assert result.open_order_ids == ["stop-1"]
    assert result.highest_price_observed == 305.0  # ratcheted from current price, no prior/hwm


def test_duplicate_protective_orders_halts(monkeypatch):
    store = use_fake_state(monkeypatch)
    monkeypatch.setattr(market_data, "is_market_open", lambda: True)
    position = PositionSummary(symbol="TSLA", qty=10, avg_entry_price=300.0, market_value=3050, unrealized_pl=50, side="long")
    monkeypatch.setattr(account, "get_positions", lambda: [position])
    o1 = OpenOrderSummary(id="stop-1", symbol="TSLA", side="sell", qty=10, filled_qty=0, order_type="stop", status="accepted", submitted_at=None, stop_price=270.0)
    o2 = OpenOrderSummary(id="stop-2", symbol="TSLA", side="sell", qty=10, filled_qty=0, order_type="stop", status="accepted", submitted_at=None, stop_price=270.0)
    monkeypatch.setattr(account, "get_open_orders", lambda: [o1, o2])
    monkeypatch.setattr(market_data, "get_current_price", lambda symbol: 305.0)

    import trailing_stop_strategy
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", fail_if_called)

    result = run_monitoring_cycle(make_strategy(), RISK)
    assert result.action == "halted"
    assert "protective orders" in result.reason
    assert store["data"]["TSLA"]["halted"] is True


def test_stop_moved_down_halts(monkeypatch):
    store = use_fake_state(monkeypatch, {"TSLA": {"halted": False, "halt_reason": None, "highest_price_observed": 330.0, "last_stop_level": 300.0, "last_run_at": None}})
    monkeypatch.setattr(market_data, "is_market_open", lambda: True)
    position = PositionSummary(symbol="TSLA", qty=10, avg_entry_price=300.0, market_value=3000, unrealized_pl=0, side="long")
    monkeypatch.setattr(account, "get_positions", lambda: [position])
    stop_order = OpenOrderSummary(id="stop-1", symbol="TSLA", side="sell", qty=10, filled_qty=0, order_type="stop", status="accepted", submitted_at=None, stop_price=290.0)  # lower than prior 300.0
    monkeypatch.setattr(account, "get_open_orders", lambda: [stop_order])
    monkeypatch.setattr(market_data, "get_current_price", lambda symbol: 305.0)

    import trailing_stop_strategy
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", fail_if_called)

    result = run_monitoring_cycle(make_strategy(), RISK)
    assert result.action == "halted"
    assert "decreased" in result.reason
    assert store["data"]["TSLA"]["halted"] is True


def test_trailing_stop_math_mismatch_halts(monkeypatch):
    use_fake_state(monkeypatch)
    monkeypatch.setattr(market_data, "is_market_open", lambda: True)
    position = PositionSummary(symbol="TSLA", qty=10, avg_entry_price=300.0, market_value=3630, unrealized_pl=330, side="long")
    monkeypatch.setattr(account, "get_positions", lambda: [position])
    # hwm=340, trail_percent=5 implies stop_price should be 323.0 - report something wildly different
    bad_trailing = OpenOrderSummary(id="trail-1", symbol="TSLA", side="sell", qty=10, filled_qty=0, order_type="trailing_stop", status="accepted", submitted_at=None, stop_price=300.0, trail_percent=5.0, hwm=340.0)
    monkeypatch.setattr(account, "get_open_orders", lambda: [bad_trailing])
    monkeypatch.setattr(market_data, "get_current_price", lambda symbol: 335.0)

    import trailing_stop_strategy
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", fail_if_called)

    result = run_monitoring_cycle(make_strategy(), RISK)
    assert result.action == "halted"
    assert "mismatch" in result.reason


def test_unexpected_exception_from_strategy_halts(monkeypatch):
    store = use_fake_state(monkeypatch)
    monkeypatch.setattr(market_data, "is_market_open", lambda: True)
    monkeypatch.setattr(account, "get_positions", lambda: [])
    monkeypatch.setattr(account, "get_open_orders", lambda: [])
    monkeypatch.setattr(market_data, "get_current_price", lambda symbol: 300.0)

    import trailing_stop_strategy
    def boom(self, risk_mgr):
        raise RuntimeError("something the monitor never anticipated")
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", boom)

    result = run_monitoring_cycle(make_strategy(), RISK)
    assert result.action == "halted"
    assert "RuntimeError" in result.reason
    assert store["data"]["TSLA"]["halted"] is True


def test_halt_persists_across_separate_invocations(monkeypatch):
    store = use_fake_state(monkeypatch)
    monkeypatch.setattr(market_data, "is_market_open", lambda: True)
    monkeypatch.setattr(account, "get_positions", lambda: [])
    monkeypatch.setattr(account, "get_open_orders", lambda: [])
    monkeypatch.setattr(market_data, "get_current_price", lambda symbol: 300.0)

    import trailing_stop_strategy
    def boom(self, risk_mgr):
        raise RuntimeError("trip the halt")
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", boom)

    first = run_monitoring_cycle(make_strategy(), RISK)
    assert first.action == "halted"

    # Second invocation, fresh strategy instance (simulating a fresh process) -
    # should see the persisted halt and skip without calling anything.
    monkeypatch.setattr(market_data, "is_market_open", fail_if_called)
    monkeypatch.setattr(account, "get_positions", fail_if_called)
    second = run_monitoring_cycle(make_strategy(), RISK)
    assert second.action == "halted_skip"
