"""Tests for the congress-mirror market-hours gate - confirms the
strategy is never invoked outside market hours or while halted."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import market_data
import mirror_state
from congress_monitor import run_congress_mirror_cycle
from risk_manager import RiskLimits, RiskManager

RISK = RiskManager(RiskLimits(
    max_position_size=10, max_dollar_exposure=1000, max_portfolio_exposure=1000,
    max_daily_loss=1000, max_simultaneous_positions=5, max_order_size=1000,
))


def use_fake_state(monkeypatch, initial: dict = None):
    store = {"data": initial or dict(mirror_state.DEFAULT_STATE, seen_transaction_ids=[])}
    monkeypatch.setattr(mirror_state, "load_state", lambda: dict(store["data"]))
    monkeypatch.setattr(mirror_state, "save_state", lambda state: store.update(data=state))
    return store


class FakeStrategy:
    def __init__(self):
        self.called = False

    def run_cycle(self, risk_mgr):
        self.called = True
        raise AssertionError("must not be called outside market hours or while halted")


def test_market_closed_never_invokes_the_strategy(monkeypatch):
    use_fake_state(monkeypatch)
    monkeypatch.setattr(market_data, "is_market_open", lambda: False)
    strategy = FakeStrategy()

    result = run_congress_mirror_cycle(strategy, RISK)
    assert result.action == "no_action"
    assert "market closed" in result.reason
    assert strategy.called is False


def test_halted_never_invokes_the_strategy(monkeypatch):
    use_fake_state(monkeypatch, {**mirror_state.DEFAULT_STATE, "halted": True, "halt_reason": "prior issue", "seen_transaction_ids": []})
    monkeypatch.setattr(market_data, "is_market_open", lambda: True)
    strategy = FakeStrategy()

    result = run_congress_mirror_cycle(strategy, RISK)
    assert result.action == "halted_skip"
    assert strategy.called is False


def test_market_open_and_not_halted_invokes_the_strategy(monkeypatch):
    use_fake_state(monkeypatch)
    monkeypatch.setattr(market_data, "is_market_open", lambda: True)

    class OkStrategy:
        def run_cycle(self, risk_mgr):
            from trailing_stop_strategy import CycleResult
            return CycleResult("no_action", "nothing to do")

    result = run_congress_mirror_cycle(OkStrategy(), RISK)
    assert result.action == "no_action"
    assert result.reason == "nothing to do"
