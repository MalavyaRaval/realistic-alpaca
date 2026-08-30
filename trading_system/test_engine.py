"""Integration tests: strategy signal -> engine -> risk -> order_manager.

No concrete strategy exists yet, so these tests play the strategy's role by
handing the engine a Signal and OrderIntent directly - exactly what a real
strategy would eventually produce.
"""

import engine
import order_manager
from risk_manager import RiskLimits, RiskManager
from strategy import OrderIntent, Signal

GENEROUS_LIMITS = RiskLimits(
    max_position_size=1000,
    max_dollar_exposure=50_000,
    max_portfolio_exposure=50_000,
    max_daily_loss=1_000_000,
    max_simultaneous_positions=50,
    max_order_size=50_000,
)

STINGY_LIMITS = RiskLimits(
    max_position_size=1000,
    max_dollar_exposure=50_000,
    max_portfolio_exposure=50_000,
    max_daily_loss=1_000_000,
    max_simultaneous_positions=50,
    max_order_size=1.00,  # nothing costing more than $1 gets through
)

ENGINE_TEST_SYMBOL = "PBR"


def test_hold_signal_produces_no_order():
    result = engine.process_signal("test-strategy", Signal.HOLD, None, RiskManager(GENEROUS_LIMITS))
    assert result.approved is None
    assert result.order_result is None


def test_no_action_signal_produces_no_order():
    result = engine.process_signal("test-strategy", Signal.NO_ACTION, None, RiskManager(GENEROUS_LIMITS))
    assert result.approved is None
    assert result.order_result is None


def test_buy_rejected_by_risk_never_reaches_broker():
    intent = OrderIntent(symbol=ENGINE_TEST_SYMBOL, side="buy", qty=1, order_type="market")
    result = engine.process_signal("test-strategy", Signal.BUY, intent, RiskManager(STINGY_LIMITS))
    assert result.approved is False
    assert result.order_result is None


def test_buy_approved_and_submitted_then_cleaned_up():
    from market_data import get_current_price

    price = get_current_price(ENGINE_TEST_SYMBOL)
    safe_limit_price = round(price * 0.5, 2)  # far below market, will not fill

    intent = OrderIntent(
        symbol=ENGINE_TEST_SYMBOL, side="buy", qty=1,
        order_type="limit", limit_price=safe_limit_price,
    )
    result = engine.process_signal("test-strategy", Signal.BUY, intent, RiskManager(GENEROUS_LIMITS))
    assert result.approved is True
    assert result.order_result is not None
    assert result.order_result.accepted, result.order_result.reason

    order_manager.cancel_order(result.order_result.order_id)
