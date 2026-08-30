"""Tests for FIFO buy/sell reconstruction."""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from position_reconstruction import reconstruct


@dataclass
class FakeTxn:
    id: str
    transaction_date: str
    disclosure_date: str
    transaction_type: str
    amount_range_low: Optional[float]
    amount_range_high: Optional[float]


def txn(id, date, disclosure, ttype, low, high):
    return FakeTxn(id=id, transaction_date=date, disclosure_date=disclosure, transaction_type=ttype, amount_range_low=low, amount_range_high=high)


def test_simple_buy_then_full_sell():
    txns = [
        txn("b1", "2024-01-01", "2024-01-20", "Purchase", 1001, 15000),
        txn("s1", "2024-06-01", "2024-06-15", "Sale (Full)", 1001, 15000),
    ]
    result = reconstruct(txns)
    assert len(result.realized_trades) == 1
    trade = result.realized_trades[0]
    assert trade.buy_transaction_id == "b1"
    assert trade.sell_transaction_id == "s1"
    assert trade.dollar_amount == 8000.5  # midpoint of 1001-15000
    assert trade.holding_period_days == 152  # 2024-01-01 to 2024-06-01 (2024 is a leap year)
    assert len(result.open_lots) == 0
    assert result.unmatched_sale_amount == 0.0


def test_buy_then_partial_sell_leaves_open_lot():
    txns = [
        txn("b1", "2024-01-01", "2024-01-20", "Purchase", 50001, 100000),   # midpoint 75000.5
        txn("s1", "2024-03-01", "2024-03-15", "Sale (Partial)", 1001, 15000),  # midpoint 8000.5
    ]
    result = reconstruct(txns)
    assert len(result.realized_trades) == 1
    assert result.realized_trades[0].dollar_amount == 8000.5
    assert len(result.open_lots) == 1
    assert result.open_lots[0].remaining_amount == 75000.5 - 8000.5
    assert result.open_lots[0].transaction_id == "b1"


def test_sell_spans_two_buys_fifo_order():
    txns = [
        txn("b1", "2024-01-01", "2024-01-20", "Purchase", 1001, 15000),    # 8000.5
        txn("b2", "2024-02-01", "2024-02-20", "Purchase", 1001, 15000),    # 8000.5
        txn("s1", "2024-06-01", "2024-06-15", "Sale (Full)", 15001, 50000),  # 32500.5 -> spans both buys + more
    ]
    result = reconstruct(txns)
    assert len(result.realized_trades) == 2
    assert result.realized_trades[0].buy_transaction_id == "b1"  # oldest matched first
    assert result.realized_trades[0].dollar_amount == 8000.5
    assert result.realized_trades[1].buy_transaction_id == "b2"
    assert result.realized_trades[1].dollar_amount == 8000.5
    # sale (32500.5) exceeded the two lots combined (16001) - the excess is unmatched
    assert result.unmatched_sale_amount == 32500.5 - 16001


def test_sell_with_no_prior_buy_is_not_fabricated():
    txns = [
        txn("s1", "2024-01-01", "2024-01-20", "Sale (Full)", 1001, 15000),
    ]
    result = reconstruct(txns)
    assert len(result.realized_trades) == 0
    assert result.unmatched_sale_amount == 8000.5
    assert len(result.open_lots) == 0


def test_transaction_with_missing_amount_is_skipped_not_guessed():
    txns = [
        txn("b1", "2024-01-01", "2024-01-20", "Purchase", None, None),
        txn("s1", "2024-06-01", "2024-06-15", "Sale (Full)", 1001, 15000),
    ]
    result = reconstruct(txns)
    # the buy had no amount, so it never became an open lot - the sell is unmatched
    assert len(result.realized_trades) == 0
    assert result.unmatched_sale_amount == 8000.5


def test_multiple_buys_no_sells_all_stay_open():
    txns = [
        txn("b1", "2024-01-01", "2024-01-20", "Purchase", 1001, 15000),
        txn("b2", "2024-02-01", "2024-02-20", "Purchase", 15001, 50000),
    ]
    result = reconstruct(txns)
    assert len(result.realized_trades) == 0
    assert len(result.open_lots) == 2
