"""Live tests against the paper account - read-only."""

import account


def test_get_account_summary_shape():
    summary = account.get_account_summary()
    assert summary.equity >= 0
    assert summary.cash >= 0
    assert isinstance(summary.status, str)
    assert isinstance(summary.trading_blocked, bool)
    # daily_pl is derived, not fetched - sanity check the math
    assert summary.daily_pl == summary.equity - summary.last_equity


def test_get_positions_returns_list_of_position_summaries():
    positions = account.get_positions()
    assert isinstance(positions, list)
    for p in positions:
        assert isinstance(p.symbol, str)
        assert isinstance(p.qty, float)


def test_get_open_orders_returns_list_of_order_summaries():
    orders = account.get_open_orders()
    assert isinstance(orders, list)
    for o in orders:
        assert o.side in ("buy", "sell")
        assert isinstance(o.status, str)
