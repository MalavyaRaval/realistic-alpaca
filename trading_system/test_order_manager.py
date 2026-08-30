"""Live tests against the paper account for order lifecycle.

Careful by construction: every order placed here is either blocked before
reaching the broker (duplicate test) or a limit order priced well away from
the market and cancelled immediately after (lifecycle test), so no test run
is expected to consume meaningful buying power or actually fill.
"""

import account
import market_data
import order_manager
from strategy import OrderIntent

# F already has a real open order from earlier manual testing - perfect for
# proving the duplicate guard blocks a second one without hitting the broker.
DUPLICATE_TEST_SYMBOL = "F"
LIFECYCLE_TEST_SYMBOL = "NU"


def test_duplicate_order_is_blocked_without_hitting_broker():
    open_orders = account.get_open_orders()
    assert any(o.symbol == DUPLICATE_TEST_SYMBOL for o in open_orders), (
        f"expected an existing open order for {DUPLICATE_TEST_SYMBOL} to test against"
    )

    result = order_manager.submit_order(
        OrderIntent(symbol=DUPLICATE_TEST_SYMBOL, side="buy", qty=1)
    )
    assert result.accepted is False
    assert result.status == "blocked_duplicate"
    assert "already exists" in result.reason


def test_submit_modify_cancel_lifecycle():
    price = market_data.get_current_price(LIFECYCLE_TEST_SYMBOL)
    safe_limit_price = round(price * 0.5, 2)  # far enough below market to never fill

    submit_result = order_manager.submit_order(
        OrderIntent(
            symbol=LIFECYCLE_TEST_SYMBOL, side="buy", qty=1,
            order_type="limit", limit_price=safe_limit_price,
        )
    )
    assert submit_result.accepted, submit_result.reason
    live_order_id = submit_result.order_id

    # Alpaca only allows replacing orders in certain broker-side states
    # (e.g. "new"); while the market is closed, DAY orders often sit in
    # "accepted" and replacement is rejected. Both outcomes are a valid
    # pass here - what matters is a clean, structured result either way
    # (never a crash), and that the order can still be cancelled after.
    modify_result = order_manager.modify_order(
        live_order_id, limit_price=round(safe_limit_price * 0.9, 2)
    )
    if modify_result.accepted:
        # Replacing an order gives it a new id - the original becomes "replaced".
        live_order_id = modify_result.order_id
    else:
        assert modify_result.status == "modify_rejected"
        assert modify_result.reason

    assert order_manager.cancel_order(live_order_id)

    verify_result = order_manager.verify_fill(live_order_id, timeout=10, poll_interval=1)
    assert verify_result.accepted is False
    assert verify_result.status == "canceled"


def test_verify_fill_times_out_gracefully_on_pending_order():
    open_orders = account.get_open_orders()
    pending = next((o for o in open_orders if o.symbol in ("NOK", "SOFI")), None)
    assert pending is not None, "expected a pending NOK/SOFI order from earlier manual testing"

    result = order_manager.verify_fill(pending.id, timeout=3, poll_interval=1)
    assert result.accepted is False
    assert result.status in ("accepted", "new", "pending_new") or "timed out" in (result.reason or "")
