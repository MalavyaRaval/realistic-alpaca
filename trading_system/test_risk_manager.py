"""Pure unit tests for risk_manager - no network calls, no live account."""

from account import AccountSummary, PositionSummary
from risk_manager import RiskLimits, RiskManager
from strategy import OrderIntent


def make_account(equity=10_000, cash=10_000, buying_power=10_000, last_equity=10_000, **kw):
    defaults = dict(
        equity=equity, cash=cash, buying_power=buying_power, last_equity=last_equity,
        status="ACTIVE", trading_blocked=False, account_blocked=False,
        pattern_day_trader=False, daytrade_count=0,
    )
    defaults.update(kw)
    return AccountSummary(**defaults)


def make_limits(**overrides):
    defaults = dict(
        max_position_size=100,
        max_dollar_exposure=5_000,
        max_portfolio_exposure=8_000,
        max_daily_loss=500,
        max_simultaneous_positions=5,
        max_order_size=2_000,
    )
    defaults.update(overrides)
    return RiskLimits(**defaults)


def make_intent(symbol="AAPL", side="buy", qty=1, order_type="market", limit_price=None):
    return OrderIntent(symbol=symbol, side=side, qty=qty, order_type=order_type, limit_price=limit_price)


def test_valid_order_is_approved():
    rm = RiskManager(make_limits())
    decision = rm.evaluate(make_intent(qty=5), current_price=100.0, account=make_account(), positions=[])
    assert decision.approved
    assert decision.order_notional == 500.0


def test_blocked_account_is_rejected():
    rm = RiskManager(make_limits())
    decision = rm.evaluate(
        make_intent(), current_price=100.0,
        account=make_account(trading_blocked=True), positions=[],
    )
    assert not decision.approved
    assert "blocked" in decision.reason


def test_max_daily_loss_blocks_all_new_orders():
    rm = RiskManager(make_limits(max_daily_loss=100))
    account = make_account(equity=9_800, last_equity=10_000)  # down $200 today
    decision = rm.evaluate(make_intent(qty=1), current_price=10.0, account=account, positions=[])
    assert not decision.approved
    assert "daily" in decision.reason.lower()


def test_max_order_size_rejects_oversized_single_order():
    rm = RiskManager(make_limits(max_order_size=500))
    decision = rm.evaluate(
        make_intent(qty=10), current_price=100.0, account=make_account(), positions=[]
    )  # notional = 1000 > 500
    assert not decision.approved
    assert "max_order_size" in decision.reason


def test_max_order_size_does_not_block_sell_side_exit():
    # A sell that closes/reduces a position is risk-reducing, not risk-
    # taking - it must never be blocked by max_order_size (an exit/stop
    # order must always be able to get out), even if price has risen a lot
    # since entry and the exit notional now exceeds the cap.
    rm = RiskManager(make_limits(max_order_size=500))
    positions = [PositionSummary(symbol="TSLA", qty=10, avg_entry_price=300, market_value=4000, unrealized_pl=1000, side="long")]
    decision = rm.evaluate(
        OrderIntent(symbol="TSLA", side="sell", qty=10), current_price=400.0,
        account=make_account(), positions=positions,
    )  # notional = 4000, well above the 500 cap, but it's a full close
    assert decision.approved


def test_insufficient_buying_power_rejected():
    rm = RiskManager(make_limits())
    account = make_account(buying_power=50.0)
    decision = rm.evaluate(make_intent(qty=1), current_price=100.0, account=account, positions=[])
    assert not decision.approved
    assert "buying power" in decision.reason


def test_max_position_size_uses_existing_position():
    rm = RiskManager(make_limits(max_position_size=10))
    positions = [PositionSummary(symbol="AAPL", qty=8, avg_entry_price=100, market_value=800, unrealized_pl=0, side="long")]
    decision = rm.evaluate(make_intent(qty=5), current_price=100.0, account=make_account(), positions=positions)
    # existing 8 + buying 5 = 13 > max 10
    assert not decision.approved
    assert "max_position_size" in decision.reason


def test_max_dollar_exposure_rejects():
    rm = RiskManager(make_limits(max_dollar_exposure=1_000, max_position_size=1000))
    decision = rm.evaluate(make_intent(qty=20), current_price=100.0, account=make_account(), positions=[])
    # exposure = 2000 > 1000
    assert not decision.approved
    assert "max_dollar_exposure" in decision.reason


def test_max_portfolio_exposure_accounts_for_other_positions():
    rm = RiskManager(make_limits(max_portfolio_exposure=1_000, max_dollar_exposure=10_000, max_position_size=1000))
    positions = [PositionSummary(symbol="MSFT", qty=5, avg_entry_price=150, market_value=750, unrealized_pl=0, side="long")]
    decision = rm.evaluate(make_intent(symbol="AAPL", qty=3), current_price=100.0, account=make_account(), positions=positions)
    # other positions 750 + new 300 = 1050 > 1000
    assert not decision.approved
    assert "max_portfolio_exposure" in decision.reason


def test_max_simultaneous_positions_blocks_new_symbol():
    rm = RiskManager(make_limits(max_simultaneous_positions=1))
    positions = [PositionSummary(symbol="MSFT", qty=5, avg_entry_price=150, market_value=750, unrealized_pl=0, side="long")]
    decision = rm.evaluate(make_intent(symbol="AAPL", qty=1), current_price=100.0, account=make_account(), positions=positions)
    assert not decision.approved
    assert "max_simultaneous_positions" in decision.reason


def test_max_simultaneous_positions_allows_adding_to_existing_symbol():
    rm = RiskManager(make_limits(max_simultaneous_positions=1))
    positions = [PositionSummary(symbol="AAPL", qty=5, avg_entry_price=150, market_value=750, unrealized_pl=0, side="long")]
    decision = rm.evaluate(make_intent(symbol="AAPL", qty=1), current_price=100.0, account=make_account(), positions=positions)
    assert decision.approved


def test_sell_of_existing_position_is_not_blocked_by_position_count_cap():
    # Already at the max_simultaneous_positions cap via this one AAPL
    # position - selling it down should not be treated as "opening a new
    # position slot", since the symbol count doesn't increase.
    rm = RiskManager(make_limits(max_simultaneous_positions=1))
    positions = [PositionSummary(symbol="AAPL", qty=5, avg_entry_price=150, market_value=750, unrealized_pl=0, side="long")]
    decision = rm.evaluate(
        OrderIntent(symbol="AAPL", side="sell", qty=5), current_price=100.0,
        account=make_account(), positions=positions,
    )
    assert decision.approved
