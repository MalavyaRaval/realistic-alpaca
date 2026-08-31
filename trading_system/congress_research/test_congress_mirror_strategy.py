"""Tests for the congress-copy strategy's validation gates and cycle
logic. Every external boundary (database, broker checks, source
verification, market data, account state, TrailingStopStrategy) is
monkeypatched, so nothing here touches the live account, the network,
or the real congress_trades.db.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import account
import congress_mirror_strategy as cms
import database
import market_data
import mirror_state
import order_manager
import trailing_stop_strategy
from congress_mirror_strategy import CongressCopyParams, CongressCopyStrategy
from database import CongressTransaction
from risk_manager import RiskLimits, RiskManager
from trailing_stop_strategy import CycleResult

OUTER_RISK = RiskManager(RiskLimits(
    max_position_size=0, max_dollar_exposure=0, max_portfolio_exposure=0,
    max_daily_loss=1000, max_simultaneous_positions=0, max_order_size=0,
))


def make_strategy(dry_run=True, **overrides):
    return CongressCopyStrategy(CongressCopyParams(**overrides), dry_run=dry_run)


def make_candidate(
    id="tx1", ticker="NVDA", chamber="house", politician="Test Person",
    asset_type="Stock", disclosure_age_days=14,
    source_document_url="https://disclosures-clerk.house.gov/example.pdf",
):
    return CongressTransaction(
        id=id, source="house_clerk", chamber=chamber, politician_name=politician,
        politician_id="house_test", party="D", state="CA", office="U.S. Representative",
        owner="Self", transaction_date="2026-08-01", disclosure_date="2026-08-15",
        disclosure_age_days=disclosure_age_days, source_reported_days_to_file=14, is_late_filing=False,
        ticker=ticker, security_name=f"{ticker} Inc.", asset_type=asset_type,
        transaction_type="Purchase", amount_range_low=1001, amount_range_high=15000,
        amount_range_label="$1,001 - $15,000", comment=None,
        source_document_url=source_document_url,
        ingested_at="2026-08-16T00:00:00+00:00",
    )


def make_account_summary(equity=100.0, cash=100.0, buying_power=100.0):
    return account.AccountSummary(
        equity=equity, cash=cash, buying_power=buying_power, last_equity=equity,
        status="ACTIVE", trading_blocked=False, account_blocked=False,
        pattern_day_trader=False, daytrade_count=0,
    )


def make_position(symbol, market_value, qty=1.0):
    return account.PositionSummary(
        symbol=symbol, qty=qty, avg_entry_price=market_value / qty,
        market_value=market_value, unrealized_pl=0.0, side="long",
    )


def make_open_order(symbol, side="buy", qty=1.0, order_type="market", status="new"):
    return account.OpenOrderSummary(
        id=f"o-{symbol}", symbol=symbol, side=side, qty=qty, filled_qty=0,
        order_type=order_type, status=status, submitted_at=None,
    )


def use_fake_state(monkeypatch, initial: dict = None):
    store = {"data": initial or dict(mirror_state.DEFAULT_STATE, seen_transaction_ids=[], open_position_tickers=[])}
    monkeypatch.setattr(mirror_state, "load_state", lambda: dict(store["data"]))
    monkeypatch.setattr(mirror_state, "save_state", lambda state: store.update(data=dict(state)))
    return store


def fail_if_called(*a, **k):
    raise AssertionError("must not be called")


def patch_happy_gates(monkeypatch, price=1.0):
    """All external gates default to passing - individual tests override
    one piece at a time to exercise its specific rejection reason."""
    monkeypatch.setattr(cms, "verify_source_document", lambda url, timeout=10.0: (True, "verified"))
    monkeypatch.setattr(cms, "is_confirmed_equity", lambda t: True)
    monkeypatch.setattr(cms, "is_options_disclosure", lambda t: False)
    monkeypatch.setattr(cms, "check_tradable", lambda client, ticker: (True, "tradable"))
    monkeypatch.setattr(cms, "get_trading_client", lambda: object())
    monkeypatch.setattr(market_data, "get_current_price", lambda ticker: price)
    monkeypatch.setattr(account, "get_account_summary", lambda: make_account_summary())
    monkeypatch.setattr(account, "get_positions", lambda: [])
    monkeypatch.setattr(account, "get_open_orders", lambda: [])


# --- decide_candidate(): each validation gate in isolation ---

def test_decide_candidate_approves_and_sizes_correctly(monkeypatch):
    patch_happy_gates(monkeypatch, price=1.0)
    candidate = make_candidate()
    acct = make_account_summary(equity=100.0, cash=100.0, buying_power=100.0)

    dec = make_strategy().decide_candidate(candidate, acct, positions=[], open_position_tickers=[], pending_notional=0.0)

    # 5% of $100 equity = $5 security cap, binds tighter than the $20
    # strategy cap and $100 cash/buying-power at $1.00/share -> 5 shares.
    assert dec.approved is True
    assert dec.qty == 5
    assert dec.dollar_amount == 5.0


def test_decide_candidate_rejects_when_position_size_rounds_to_zero(monkeypatch):
    # The realistic $100-account scenario: 5% security cap is $5, but a
    # $29 share price (e.g. a typical mid-cap stock) can't buy even one
    # share within that cap.
    patch_happy_gates(monkeypatch, price=29.0)
    candidate = make_candidate()
    acct = make_account_summary(equity=100.0, cash=100.0, buying_power=100.0)

    dec = make_strategy().decide_candidate(candidate, acct, positions=[], open_position_tickers=[], pending_notional=0.0)
    assert dec.approved is False
    assert "rounds to 0 shares" in dec.reason


def test_decide_candidate_cash_cap_binds_tighter_than_security_cap(monkeypatch):
    patch_happy_gates(monkeypatch, price=1.0)
    candidate = make_candidate()
    acct = make_account_summary(equity=1000.0, cash=3.0, buying_power=1000.0)

    dec = make_strategy().decide_candidate(candidate, acct, positions=[], open_position_tickers=[], pending_notional=0.0)
    # security cap = $50, strategy cap = $200, but cash is only $3 -
    # cash wins regardless of buying_power being far larger (never margin).
    assert dec.approved is True
    assert dec.qty == 3
    assert dec.dollar_amount == 3.0


def test_decide_candidate_buying_power_binds_tighter_than_cash(monkeypatch):
    # A live account can report buying_power BELOW cash (e.g. holds from
    # other pending activity) - the smaller of the two must always win,
    # since buying_power is the broker's own real-time figure for what
    # it will actually let this specific order commit.
    patch_happy_gates(monkeypatch, price=1.0)
    candidate = make_candidate()
    acct = make_account_summary(equity=1000.0, cash=1000.0, buying_power=4.0)

    dec = make_strategy().decide_candidate(candidate, acct, positions=[], open_position_tickers=[], pending_notional=0.0)
    assert dec.approved is True
    assert dec.qty == 4


def test_decide_candidate_strategy_cap_binds_when_other_positions_already_deployed(monkeypatch):
    patch_happy_gates(monkeypatch, price=1.0)
    candidate = make_candidate(ticker="NEW")
    acct = make_account_summary(equity=100.0, cash=100.0, buying_power=100.0)
    positions = [make_position("OTHER", market_value=17.0)]

    dec = make_strategy().decide_candidate(candidate, acct, positions=positions, open_position_tickers=["OTHER"], pending_notional=0.0)
    # strategy cap = 100*0.20 - 17 already deployed = $3, tighter than the $5 security cap.
    assert dec.approved is True
    assert dec.qty == 3


def test_decide_candidate_strategy_cap_accounts_for_pending_unfilled_orders(monkeypatch):
    # The confirmed audit finding: a same-strategy order that hasn't
    # filled yet must still count against the aggregate strategy cap,
    # not just filled positions' market_value.
    patch_happy_gates(monkeypatch, price=1.0)
    candidate = make_candidate(ticker="NEW")
    acct = make_account_summary(equity=100.0, cash=100.0, buying_power=100.0)

    dec = make_strategy().decide_candidate(
        candidate, acct, positions=[], open_position_tickers=["PENDING_TICKER"], pending_notional=17.0,
    )
    # strategy cap = 100*0.20 - 17 pending = $3, tighter than the $5 security cap.
    assert dec.approved is True
    assert dec.qty == 3


def test_decide_candidate_rejects_when_pending_notional_unknown(monkeypatch):
    patch_happy_gates(monkeypatch, price=1.0)
    candidate = make_candidate()
    acct = make_account_summary()

    dec = make_strategy().decide_candidate(candidate, acct, positions=[], open_position_tickers=[], pending_notional=None)
    assert dec.approved is False
    assert "cannot verify" in dec.reason


def test_decide_candidate_rejects_at_max_positions(monkeypatch):
    params = CongressCopyParams(max_positions=2)
    candidate = make_candidate(ticker="THIRD")
    acct = make_account_summary()

    dec = CongressCopyStrategy(params).decide_candidate(candidate, acct, positions=[], open_position_tickers=["A", "B"], pending_notional=0.0)
    assert dec.approved is False
    assert "max_positions" in dec.reason


def test_decide_candidate_rejects_duplicate_ticker(monkeypatch):
    candidate = make_candidate(ticker="NVDA")
    acct = make_account_summary()

    dec = make_strategy().decide_candidate(candidate, acct, positions=[], open_position_tickers=["NVDA"], pending_notional=0.0)
    assert dec.approved is False
    assert "already holding" in dec.reason


def test_decide_candidate_rejects_failed_source_verification(monkeypatch):
    patch_happy_gates(monkeypatch)
    monkeypatch.setattr(cms, "verify_source_document", lambda url, timeout=10.0: (False, "404 not found"))
    candidate = make_candidate()
    acct = make_account_summary()

    dec = make_strategy().decide_candidate(candidate, acct, positions=[], open_position_tickers=[], pending_notional=0.0)
    assert dec.approved is False
    assert "source verification failed" in dec.reason
    assert "404 not found" in dec.reason


def test_decide_candidate_rejects_missing_disclosure_age(monkeypatch):
    patch_happy_gates(monkeypatch)
    candidate = make_candidate(disclosure_age_days=None)
    acct = make_account_summary()

    dec = make_strategy().decide_candidate(candidate, acct, positions=[], open_position_tickers=[], pending_notional=0.0)
    assert dec.approved is False
    assert "disclosure age could not be determined" in dec.reason


def test_decide_candidate_rejects_stale_disclosure(monkeypatch):
    patch_happy_gates(monkeypatch)
    candidate = make_candidate(disclosure_age_days=60)
    acct = make_account_summary()

    dec = CongressCopyStrategy(CongressCopyParams(max_disclosure_age_days=45)).decide_candidate(
        candidate, acct, positions=[], open_position_tickers=[], pending_notional=0.0,
    )
    assert dec.approved is False
    assert "stale" in dec.reason
    assert "60d" in dec.reason


def test_decide_candidate_rejects_options_disclosure(monkeypatch):
    patch_happy_gates(monkeypatch)
    monkeypatch.setattr(cms, "is_confirmed_equity", lambda t: False)
    monkeypatch.setattr(cms, "is_options_disclosure", lambda t: True)
    candidate = make_candidate(asset_type="Stock Option")
    acct = make_account_summary()

    dec = make_strategy().decide_candidate(candidate, acct, positions=[], open_position_tickers=[], pending_notional=0.0)
    assert dec.approved is False
    assert "options disclosure" in dec.reason


def test_decide_candidate_rejects_unconfirmed_asset_type(monkeypatch):
    patch_happy_gates(monkeypatch)
    monkeypatch.setattr(cms, "is_confirmed_equity", lambda t: False)
    monkeypatch.setattr(cms, "is_options_disclosure", lambda t: False)
    candidate = make_candidate(asset_type="Corporate Bond")
    acct = make_account_summary()

    dec = make_strategy().decide_candidate(candidate, acct, positions=[], open_position_tickers=[], pending_notional=0.0)
    assert dec.approved is False
    assert "not a confirmed plain-equity type" in dec.reason


def test_decide_candidate_rejects_when_not_tradable(monkeypatch):
    patch_happy_gates(monkeypatch)
    monkeypatch.setattr(cms, "check_tradable", lambda client, ticker: (False, "asset not found"))
    candidate = make_candidate()
    acct = make_account_summary()

    dec = make_strategy().decide_candidate(candidate, acct, positions=[], open_position_tickers=[], pending_notional=0.0)
    assert dec.approved is False
    assert "not tradable" in dec.reason


def test_decide_candidate_rejects_when_price_unavailable(monkeypatch):
    patch_happy_gates(monkeypatch)

    def boom(ticker):
        raise market_data.MarketDataUnavailable("feed down")
    monkeypatch.setattr(market_data, "get_current_price", boom)
    candidate = make_candidate()
    acct = make_account_summary()

    dec = make_strategy().decide_candidate(candidate, acct, positions=[], open_position_tickers=[], pending_notional=0.0)
    assert dec.approved is False
    assert "could not price" in dec.reason


# --- _pending_buy_notional() ---

def test_pending_buy_notional_sums_only_tracked_buy_orders(monkeypatch):
    monkeypatch.setattr(market_data, "get_current_price", lambda ticker: {"A": 2.0, "B": 5.0}[ticker])
    open_orders = [
        make_open_order("A", side="buy", qty=3),
        make_open_order("B", side="buy", qty=1),
        make_open_order("C", side="buy", qty=100),   # not tracked - excluded
        make_open_order("A", side="sell", qty=3),    # a protective stop - excluded
    ]
    total = make_strategy()._pending_buy_notional(open_orders, tracked_tickers=["A", "B"])
    assert total == 2.0 * 3 + 5.0 * 1


def test_pending_buy_notional_returns_none_when_price_unavailable(monkeypatch):
    def boom(ticker):
        raise market_data.MarketDataUnavailable("feed down")
    monkeypatch.setattr(market_data, "get_current_price", boom)
    open_orders = [make_open_order("A", side="buy", qty=1)]
    total = make_strategy()._pending_buy_notional(open_orders, tracked_tickers=["A"])
    assert total is None


# --- run_cycle(): full-cycle orchestration ---

def test_halted_skips_everything(monkeypatch):
    use_fake_state(monkeypatch, {**mirror_state.DEFAULT_STATE, "halted": True, "halt_reason": "test halt", "seen_transaction_ids": [], "open_position_tickers": []})
    monkeypatch.setattr(database, "get_new_purchase_candidates", fail_if_called)
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", fail_if_called)

    result = make_strategy().run_cycle(OUTER_RISK)
    assert result.action == "halted_skip"
    assert "test halt" in result.detail


def test_manages_existing_open_position_by_delegating(monkeypatch):
    use_fake_state(monkeypatch, {**mirror_state.DEFAULT_STATE, "open_position_tickers": ["NVDA"], "seen_transaction_ids": []})
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: [])
    monkeypatch.setattr(account, "get_positions", lambda: [make_position("NVDA", market_value=5.0)])
    monkeypatch.setattr(account, "get_open_orders", lambda: [])
    monkeypatch.setattr(account, "get_account_summary", lambda: make_account_summary())
    monkeypatch.setattr(
        trailing_stop_strategy.TrailingStopStrategy, "run_cycle",
        lambda self, risk_mgr: CycleResult("holding", "fixed stop still active"),
    )

    result = make_strategy().run_cycle(OUTER_RISK)
    assert result.action == "no_action"
    assert "[NVDA] holding" in result.detail


def test_exit_removes_ticker_from_open_positions(monkeypatch):
    store = use_fake_state(monkeypatch, {**mirror_state.DEFAULT_STATE, "open_position_tickers": ["NVDA"], "seen_transaction_ids": []})
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: [])
    monkeypatch.setattr(account, "get_positions", lambda: [make_position("NVDA", market_value=5.0)])
    monkeypatch.setattr(account, "get_open_orders", lambda: [])
    monkeypatch.setattr(account, "get_account_summary", lambda: make_account_summary())
    monkeypatch.setattr(
        trailing_stop_strategy.TrailingStopStrategy, "run_cycle",
        lambda self, risk_mgr: CycleResult("exited", "position closed by protective stop"),
    )

    result = make_strategy().run_cycle(OUTER_RISK)
    assert result.action == "no_action"
    assert store["data"]["open_position_tickers"] == []


def test_ambiguous_never_filled_ticker_stays_tracked_when_entry_is_blocked(monkeypatch):
    """A tracked ticker whose day order expired between this loop's own
    snapshot and TrailingStopStrategy's fresh internal check might try to
    re-enter; the dedicated no-entry risk manager should block it (here
    simulated directly), and the ticker must stay tracked for a retry
    next cycle rather than being silently dropped or, worse, actually
    re-entered."""
    store = use_fake_state(monkeypatch, {**mirror_state.DEFAULT_STATE, "open_position_tickers": ["GHOST"], "seen_transaction_ids": []})
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: [])
    monkeypatch.setattr(account, "get_positions", lambda: [])
    monkeypatch.setattr(account, "get_open_orders", lambda: [])
    monkeypatch.setattr(account, "get_account_summary", lambda: make_account_summary())
    monkeypatch.setattr(
        trailing_stop_strategy.TrailingStopStrategy, "run_cycle",
        lambda self, risk_mgr: CycleResult("entry_blocked", "blocked by no-entry risk manager"),
    )

    result = make_strategy().run_cycle(OUTER_RISK)
    assert store["data"]["open_position_tickers"] == ["GHOST"]
    assert "entry_blocked" in result.detail


def test_no_entry_risk_manager_actually_blocks_a_real_entry_attempt(monkeypatch):
    """End-to-end proof (no mocking of TrailingStopStrategy itself) that
    the reconciliation loop cannot submit a real order: a tracked ticker
    with no live position and no filled history looks, to
    TrailingStopStrategy, exactly like one that should be entered - this
    confirms the dedicated no-entry RiskManager stops it before
    order_manager.submit_order is ever reached."""
    use_fake_state(monkeypatch, {**mirror_state.DEFAULT_STATE, "open_position_tickers": ["GHOST"], "seen_transaction_ids": []})
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: [])
    monkeypatch.setattr(account, "get_positions", lambda: [])
    monkeypatch.setattr(account, "get_open_orders", lambda: [])
    monkeypatch.setattr(account, "get_closed_orders", lambda symbol, side=None, limit=50: [])
    monkeypatch.setattr(account, "get_account_summary", lambda: make_account_summary())
    monkeypatch.setattr(market_data, "get_current_price", lambda symbol: 10.0)
    monkeypatch.setattr(market_data, "get_volume", lambda symbol: 1000)
    monkeypatch.setattr(market_data, "is_market_open", lambda: True)
    # A call-tracking stub, not fail_if_called: run_cycle() wraps each
    # ticker's management in its own try/except (so one ticker's error
    # can't cost the whole cycle's reconciliation - see the dedicated
    # test for that), which would otherwise silently swallow an
    # AssertionError raised from inside submit_order and turn a real
    # violation into a soft "will retry next cycle" outcome instead of a
    # failed test.
    submit_calls = []
    monkeypatch.setattr(order_manager, "submit_order", lambda intent: submit_calls.append(intent))

    result = make_strategy().run_cycle(OUTER_RISK)
    assert submit_calls == []
    assert "entry_blocked" in result.detail
    assert "GHOST" in result.detail


def test_orphaned_live_position_is_recovered_into_tracking(monkeypatch):
    """A real, live broker position for a ticker absent from
    open_position_tickers (e.g. a prior crash between order submission
    and state persistence) must be added back and managed this cycle,
    not left permanently invisible."""
    store = use_fake_state(monkeypatch)  # open_position_tickers starts empty
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: [])
    monkeypatch.setattr(account, "get_positions", lambda: [make_position("ORPHAN", market_value=5.0)])
    monkeypatch.setattr(account, "get_open_orders", lambda: [])
    monkeypatch.setattr(account, "get_account_summary", lambda: make_account_summary())
    seen_tickers = []
    def fake_run_cycle(self, risk_mgr):
        seen_tickers.append(self.params.symbol)
        return CycleResult("holding", "initial stop placed for recovered position")
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", fake_run_cycle)

    result = make_strategy().run_cycle(OUTER_RISK)
    assert "ORPHAN" in seen_tickers
    assert store["data"]["open_position_tickers"] == ["ORPHAN"]


def test_one_ticker_error_does_not_lose_another_tickers_legitimate_exit(monkeypatch):
    """A transient error managing one tracked ticker must not roll back
    or skip committing another ticker's legitimate exit in the same
    cycle, and must not halt the whole strategy over it."""
    store = use_fake_state(monkeypatch, {**mirror_state.DEFAULT_STATE, "open_position_tickers": ["EXITS_FINE", "ERRORS"], "seen_transaction_ids": []})
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: [])
    monkeypatch.setattr(account, "get_positions", lambda: [make_position("ERRORS", market_value=5.0)])
    monkeypatch.setattr(account, "get_open_orders", lambda: [])
    monkeypatch.setattr(account, "get_account_summary", lambda: make_account_summary())

    def fake_run_cycle(self, risk_mgr):
        if self.params.symbol == "EXITS_FINE":
            return CycleResult("exited", "position closed by protective stop")
        raise RuntimeError("market data hiccup")
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", fake_run_cycle)

    result = make_strategy().run_cycle(OUTER_RISK)
    assert result.action != "halted"
    assert store["data"]["open_position_tickers"] == ["ERRORS"]
    assert store["data"]["halted"] is False


def test_pending_entry_keeps_ticker_tracked(monkeypatch):
    store = use_fake_state(monkeypatch, {**mirror_state.DEFAULT_STATE, "open_position_tickers": ["NVDA"], "seen_transaction_ids": []})
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: [])
    monkeypatch.setattr(account, "get_positions", lambda: [])
    monkeypatch.setattr(account, "get_open_orders", lambda: [make_open_order("NVDA", side="buy")])
    monkeypatch.setattr(account, "get_account_summary", lambda: make_account_summary())
    monkeypatch.setattr(market_data, "get_current_price", lambda symbol: 1.0)
    monkeypatch.setattr(
        trailing_stop_strategy.TrailingStopStrategy, "run_cycle",
        lambda self, risk_mgr: CycleResult("waiting", "entry order o1 still open, not filled yet"),
    )

    result = make_strategy().run_cycle(OUTER_RISK)
    assert store["data"]["open_position_tickers"] == ["NVDA"]


def test_no_candidates_returns_no_action(monkeypatch):
    use_fake_state(monkeypatch)
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: [])
    patch_happy_gates(monkeypatch)
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", fail_if_called)

    result = make_strategy().run_cycle(OUTER_RISK)
    assert result.action == "no_action"


def test_first_approved_candidate_is_added_to_open_positions(monkeypatch):
    store = use_fake_state(monkeypatch)
    patch_happy_gates(monkeypatch, price=1.0)
    candidate = make_candidate(id="tx1", ticker="NVDA", politician="Jane Doe")
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: [candidate])

    submitted = CycleResult("entry_submitted", "order abc123 submitted, awaiting fill")
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", lambda self, risk_mgr: submitted)

    result = make_strategy(dry_run=False).run_cycle(OUTER_RISK)
    assert result.action == "entry_submitted"
    assert "Jane Doe" in result.detail
    assert "NVDA" in result.detail
    assert store["data"]["open_position_tickers"] == ["NVDA"]
    assert "tx1" in store["data"]["seen_transaction_ids"]


def test_new_approved_candidate_is_appended_alongside_existing_open_positions(monkeypatch):
    store = use_fake_state(monkeypatch, {**mirror_state.DEFAULT_STATE, "open_position_tickers": ["OLD"], "seen_transaction_ids": []})
    patch_happy_gates(monkeypatch, price=1.0)
    monkeypatch.setattr(account, "get_positions", lambda: [make_position("OLD", market_value=5.0)])
    candidate = make_candidate(id="tx1", ticker="NEW")
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: [candidate])

    def fake_run_cycle(self, risk_mgr):
        if self.params.symbol == "OLD":
            return CycleResult("holding", "still holding")
        return CycleResult("entry_submitted", "order xyz submitted")
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", fake_run_cycle)

    result = make_strategy(dry_run=False).run_cycle(OUTER_RISK)
    assert result.action == "entry_submitted"
    assert store["data"]["open_position_tickers"] == ["OLD", "NEW"]


def test_rejected_candidate_tries_the_next_one(monkeypatch):
    store = use_fake_state(monkeypatch)
    patch_happy_gates(monkeypatch, price=1.0)
    candidates = [make_candidate(id="tx1", ticker="BLOCKED"), make_candidate(id="tx2", ticker="OK")]
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: candidates)

    def fake_check_tradable(client, ticker):
        if ticker == "BLOCKED":
            return False, "not tradable"
        return True, "tradable"
    monkeypatch.setattr(cms, "check_tradable", fake_check_tradable)
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", lambda self, risk_mgr: CycleResult("entry_submitted", "order xyz submitted"))

    result = make_strategy(dry_run=False).run_cycle(OUTER_RISK)
    assert result.action == "entry_submitted"
    assert store["data"]["open_position_tickers"] == ["OK"]
    assert "tx1" in store["data"]["seen_transaction_ids"]
    assert "tx2" in store["data"]["seen_transaction_ids"]


def test_all_candidates_rejected_returns_no_action_but_marks_all_seen(monkeypatch):
    store = use_fake_state(monkeypatch)
    patch_happy_gates(monkeypatch, price=29.0)  # rounds to 0 shares on a $100 account
    candidates = [make_candidate(id="tx1", ticker="A"), make_candidate(id="tx2", ticker="B")]
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: candidates)
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", fail_if_called)

    result = make_strategy().run_cycle(OUTER_RISK)
    assert result.action == "no_action"
    assert set(store["data"]["seen_transaction_ids"]) == {"tx1", "tx2"}


def test_unexpected_exception_halts(monkeypatch):
    store = use_fake_state(monkeypatch)

    def boom(**kw):
        raise RuntimeError("database exploded")
    monkeypatch.setattr(database, "get_new_purchase_candidates", boom)
    monkeypatch.setattr(account, "get_positions", lambda: [])
    monkeypatch.setattr(account, "get_open_orders", lambda: [])
    monkeypatch.setattr(account, "get_account_summary", lambda: make_account_summary())

    result = make_strategy().run_cycle(OUTER_RISK)
    assert result.action == "halted"
    assert "RuntimeError" in result.detail
    assert store["data"]["halted"] is True


def test_seen_ids_persist_so_a_rejected_candidate_is_not_reconsidered(monkeypatch):
    use_fake_state(monkeypatch)
    patch_happy_gates(monkeypatch, price=29.0)  # rejected on sizing every time
    candidate = make_candidate(id="tx1", ticker="A")
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", fail_if_called)

    call_count = {"n": 0}
    def get_candidates(seen_ids, chambers, limit):
        call_count["n"] += 1
        if "tx1" in seen_ids:
            return []
        return [candidate]
    monkeypatch.setattr(database, "get_new_purchase_candidates", get_candidates)

    strategy = make_strategy()
    strategy.run_cycle(OUTER_RISK)
    result2 = strategy.run_cycle(OUTER_RISK)
    assert result2.action == "no_action"
    assert call_count["n"] == 2


# --- dry_run mode ---

def test_dry_run_is_the_default():
    assert make_strategy().dry_run is True


def test_dry_run_reports_approved_candidate_without_submitting_or_marking_seen(monkeypatch):
    store = use_fake_state(monkeypatch)
    patch_happy_gates(monkeypatch, price=1.0)
    candidate = make_candidate(id="tx1", ticker="NVDA", politician="Jane Doe")
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: [candidate])
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", fail_if_called)
    monkeypatch.setattr(order_manager, "submit_order", fail_if_called)

    result = CongressCopyStrategy(CongressCopyParams(), dry_run=True).run_cycle(OUTER_RISK)

    assert result.action == "dry_run_proposal"
    assert "NVDA" in result.detail
    assert "Jane Doe" in result.detail
    assert "NO ORDER WAS SUBMITTED" in result.detail
    assert store["data"]["open_position_tickers"] == []
    assert "tx1" not in store["data"]["seen_transaction_ids"]


def test_dry_run_false_actually_submits_the_previously_dry_run_candidate(monkeypatch):
    """The core guarantee: the exact candidate shown during dry_run must
    be what actually executes once dry_run is turned off - not a
    different, later transaction."""
    store = use_fake_state(monkeypatch)
    patch_happy_gates(monkeypatch, price=1.0)
    candidate = make_candidate(id="tx1", ticker="NVDA")
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: [candidate])

    dry_result = CongressCopyStrategy(CongressCopyParams(), dry_run=True).run_cycle(OUTER_RISK)
    assert dry_result.action == "dry_run_proposal"
    assert "tx1" not in store["data"]["seen_transaction_ids"]

    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", lambda self, risk_mgr: CycleResult("entry_submitted", "order xyz submitted"))
    live_result = CongressCopyStrategy(CongressCopyParams(), dry_run=False).run_cycle(OUTER_RISK)

    assert live_result.action == "entry_submitted"
    assert "NVDA" in live_result.detail
    assert "tx1" in store["data"]["seen_transaction_ids"]


def test_dry_run_rejected_candidate_is_still_marked_seen(monkeypatch):
    store = use_fake_state(monkeypatch)
    patch_happy_gates(monkeypatch, price=29.0)  # rounds to 0 shares - rejected regardless of dry_run
    candidate = make_candidate(id="tx1", ticker="EXPENSIVE")
    monkeypatch.setattr(database, "get_new_purchase_candidates", lambda **kw: [candidate])
    monkeypatch.setattr(trailing_stop_strategy.TrailingStopStrategy, "run_cycle", fail_if_called)

    result = CongressCopyStrategy(CongressCopyParams(), dry_run=True).run_cycle(OUTER_RISK)
    assert result.action == "no_action"
    assert "tx1" in store["data"]["seen_transaction_ids"]


def test_defaults_to_house_only_chamber():
    params = CongressCopyParams()
    assert params.allowed_chambers == ("house",)


def test_defaults_match_the_configured_risk_limits():
    params = CongressCopyParams()
    assert params.max_single_security_pct == 0.05
    assert params.max_strategy_pct == 0.20
    assert params.max_positions == 10
    assert params.max_disclosure_age_days == 45
    assert params.allow_options is False
