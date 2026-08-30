"""Reconstructs disclosed buy/sell activity per (politician, ticker) into
matched round-trips and still-open lots, using FIFO matching on
disclosed dollar amounts (share counts are never disclosed - only
ranges - so position size is tracked in dollars at the range midpoint,
not share counts).

A sale with no matching open lot (e.g. a position opened before this
analysis's data window, or before STOCK Act reporting existed) is
excluded from return calculations rather than assigned a fabricated
entry price - "do not invent missing information" applies here exactly
as it does to the raw disclosure fields.
"""

from dataclasses import dataclass, field
from datetime import date as date_cls
from typing import Optional

BUY_TYPES = {"Purchase"}
SELL_TYPES = {"Sale (Full)", "Sale (Partial)"}


@dataclass
class OpenLot:
    transaction_id: str
    transaction_date: str
    disclosure_date: str
    remaining_amount: float


@dataclass
class RealizedTrade:
    buy_transaction_id: str
    sell_transaction_id: str
    buy_transaction_date: str
    buy_disclosure_date: str
    sell_transaction_date: str
    sell_disclosure_date: str
    dollar_amount: float
    holding_period_days: int


@dataclass
class ReconstructionResult:
    realized_trades: list = field(default_factory=list)
    open_lots: list = field(default_factory=list)
    unmatched_sale_amount: float = 0.0  # sales with no open lot to match against


def _amount(txn) -> Optional[float]:
    if txn.amount_range_low is None or txn.amount_range_high is None:
        return None
    return (txn.amount_range_low + txn.amount_range_high) / 2.0


def reconstruct(transactions: list) -> ReconstructionResult:
    """`transactions` must already be filtered to ONE (politician, ticker)
    pair and sorted by transaction_date ascending (ties broken by a
    stable, deterministic secondary key by the caller - see
    performance_engine.py)."""
    result = ReconstructionResult()
    open_lots: list = []

    for txn in transactions:
        amount = _amount(txn)
        if amount is None or amount <= 0:
            continue

        if txn.transaction_type in BUY_TYPES:
            open_lots.append(OpenLot(
                transaction_id=txn.id, transaction_date=txn.transaction_date,
                disclosure_date=txn.disclosure_date, remaining_amount=amount,
            ))

        elif txn.transaction_type in SELL_TYPES:
            remaining_to_sell = amount
            while remaining_to_sell > 1e-9 and open_lots:
                lot = open_lots[0]
                matched = min(lot.remaining_amount, remaining_to_sell)

                holding_days = (
                    date_cls.fromisoformat(txn.transaction_date)
                    - date_cls.fromisoformat(lot.transaction_date)
                ).days

                result.realized_trades.append(RealizedTrade(
                    buy_transaction_id=lot.transaction_id,
                    sell_transaction_id=txn.id,
                    buy_transaction_date=lot.transaction_date,
                    buy_disclosure_date=lot.disclosure_date,
                    sell_transaction_date=txn.transaction_date,
                    sell_disclosure_date=txn.disclosure_date,
                    dollar_amount=matched,
                    holding_period_days=holding_days,
                ))

                lot.remaining_amount -= matched
                remaining_to_sell -= matched
                if lot.remaining_amount <= 1e-9:
                    open_lots.pop(0)

            if remaining_to_sell > 1e-9:
                result.unmatched_sale_amount += remaining_to_sell

    result.open_lots = [lot for lot in open_lots if lot.remaining_amount > 1e-9]
    return result
