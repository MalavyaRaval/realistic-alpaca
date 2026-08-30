"""Pure logic tests for the congress-mirror strategy - database and
TrailingStopStrategy are both monkeypatched, so nothing here touches the
live account or the real congress_trades.db."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database
import mirror_state
import trailing_stop_strategy
from congress_mirror_strategy import CongressMirrorParams, CongressMirrorStrategy
from database import CongressTransaction
from risk_manager import RiskLimits, RiskManager
from trailing_stop_strategy import CycleResult

RISK = RiskManager(RiskLimits(
    max_position_size=10, max_dollar_exposure=1000, max_portfolio_exposure=1000,
    max_daily_loss=1000, max_simultaneous_positions=5, max_order_size=1000,
))


def make_strategy(**overrides):
    return CongressMirrorStrategy(CongressMirrorParams(**overrides))


def make_candidate(id="tx1", ticker="NVDA", chamber="house", politician="Test Person"):
    return CongressTransaction(
        id=id, source="house_clerk", chamber=chamber, politician_name=politician,
        politician_id="house_test", party="D", state="CA", office="U.S. Representative",
        owner="Self", transaction_date="2026-08-01", disclosure_date="2026-08-15",
        disclosure_age_days=14, source_reported_days_to_file=14, is_late_filing=False,
        ticker=ticker, security_name=f"{ticker} Inc.", asset_type="Stock",
        transaction_type="Purchase", amount_range_low=1001, amount_range_high=15000,
        amount_range_label="$1,001 - $15,000", comment=None,
        source_document_url="https://disclosures-clerk.house.gov/example.pdf",
        ingested_at="2026-08-16T00:00:00+00:00",
    )


def use_fake_state(monkeypatch, initial: dict = None):
    store = {"data": initial or dict(mirror_state.DEFAULT_STATE, seen_transaction_ids=[])}
    monkeypatch.setattr(mirror_state, "load_state", lambda: dict(store["data"]))
    monkeypatch.setattr(mirror_state, "save_state", lambda state: store.update(data=state))
    return store


def fail_if_called(*a, **k):
    raise AssertionError("must not be called")


def test_halted_skips_everything(monkeypatch):
    use_fake_state(monkeypatch, {**mirror_state.DEFAULT_STATE, "halted": True, "halt_reason": "test halt", "seen_transaction_ids": []})
    monkeypatch.setattr(database, "get_new_purchase_candidates", fail_if_called)
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", fail_if_called)

    result = make_strategy().run_cycle(RISK)
    assert result.action == "halted_skip"
    assert "test halt" in result.detail


def test_manages_existing_position_by_delegating_to_trailing_stop(monkeypatch):
    store = use_fake_state(monkeypatch, {**mirror_state.DEFAULT_STATE, "current_position_ticker": "NVDA", "seen_transaction_ids": []})
    monkeypatch.setattr(database, "get_new_purchase_candidates", fail_if_called)
    monkeypatch.setattr(
        trailing_stop_strategy.TrailingStopStrategy, "run_cycle",
        lambda self, risk_mgr: CycleResult("holding", "fixed stop still active"),
    )

    result = make_strategy().run_cycle(RISK)
    assert result.action == "holding"
    assert "[NVDA]" in result.detail
    assert store["data"]["current_position_ticker"] == "NVDA"  # unchanged, still holding


def test_exit_clears_current_position_ticker(monkeypatch):
    store = use_fake_state(monkeypatch, {**mirror_state.DEFAULT_STATE, "current_position_ticker": "NVDA", "seen_transaction_ids": []})
    monkeypatch.setattr(
        trailing_stop_strategy.TrailingStopStrategy, "run_cycle",
        lambda self, risk_mgr: CycleResult("exited", "position closed by protective stop"),
    )

    result = make_strategy().run_cycle(RISK)
    assert result.action == "exited"
    assert store["data"]["current_position_ticker"] is None


def test_no_candidates_returns_no_action(monkeypatch):
    use_fake_state(monkeypatch)
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: [])
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", fail_if_called)

    result = make_strategy().run_cycle(RISK)
    assert result.action == "no_action"


def test_first_successful_candidate_becomes_current_position(monkeypatch):
    store = use_fake_state(monkeypatch)
    candidate = make_candidate(id="tx1", ticker="NVDA", politician="Jane Doe")
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: [candidate])

    submitted = CycleResult("entry_submitted", "order abc123 submitted, awaiting fill")
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", lambda self, risk_mgr: submitted)

    result = make_strategy().run_cycle(RISK)
    assert result.action == "entry_submitted"
    assert "Jane Doe" in result.detail
    assert "NVDA" in result.detail
    assert "2026-08-01" in result.detail  # transaction date
    assert "2026-08-15" in result.detail  # disclosure date
    assert store["data"]["current_position_ticker"] == "NVDA"
    assert "tx1" in store["data"]["seen_transaction_ids"]


def test_rejected_candidate_tries_the_next_one(monkeypatch):
    store = use_fake_state(monkeypatch)
    candidates = [make_candidate(id="tx1", ticker="EXPENSIVE"), make_candidate(id="tx2", ticker="CHEAP")]
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: candidates)

    def fake_run_cycle(self, risk_mgr):
        if self.params.symbol == "EXPENSIVE":
            return CycleResult("entry_blocked", "insufficient buying power")
        return CycleResult("entry_submitted", "order xyz submitted")

    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", fake_run_cycle)

    result = make_strategy().run_cycle(RISK)
    assert result.action == "entry_submitted"
    assert "CHEAP" in result.detail
    assert store["data"]["current_position_ticker"] == "CHEAP"
    # both were marked seen - the rejected one should not be retried next cycle
    assert "tx1" in store["data"]["seen_transaction_ids"]
    assert "tx2" in store["data"]["seen_transaction_ids"]


def test_all_candidates_rejected_returns_no_action_but_marks_all_seen(monkeypatch):
    store = use_fake_state(monkeypatch)
    candidates = [make_candidate(id="tx1", ticker="A"), make_candidate(id="tx2", ticker="B")]
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: candidates)
    monkeypatch.setattr(
        trailing_stop_strategy.TrailingStopStrategy, "run_cycle",
        lambda self, risk_mgr: CycleResult("entry_blocked", "insufficient buying power"),
    )

    result = make_strategy().run_cycle(RISK)
    assert result.action == "no_action"
    assert set(store["data"]["seen_transaction_ids"]) == {"tx1", "tx2"}


def test_unexpected_exception_halts(monkeypatch):
    store = use_fake_state(monkeypatch)
    def boom(**kw):
        raise RuntimeError("database exploded")
    monkeypatch.setattr(database, "get_new_purchase_candidates", boom)

    result = make_strategy().run_cycle(RISK)
    assert result.action == "halted"
    assert "RuntimeError" in result.detail
    assert store["data"]["halted"] is True


def test_seen_ids_persist_so_a_rejected_candidate_is_not_reconsidered(monkeypatch):
    store = use_fake_state(monkeypatch)
    candidate = make_candidate(id="tx1", ticker="A")
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", lambda self, risk_mgr: CycleResult("entry_blocked", "no funds"))

    call_count = {"n": 0}
    def get_candidates(seen_ids, chambers, limit):
        call_count["n"] += 1
        if "tx1" in seen_ids:
            return []
        return [candidate]
    monkeypatch.setattr(database, "get_new_purchase_candidates", get_candidates)

    strategy = make_strategy()
    strategy.run_cycle(RISK)
    result2 = strategy.run_cycle(RISK)
    assert result2.action == "no_action"
    assert call_count["n"] == 2


def test_defaults_to_house_only_chamber():
    params = CongressMirrorParams()
    assert params.allowed_chambers == ("house",)
