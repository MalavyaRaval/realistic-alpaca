"""Risk-management module.

Pure logic: no network calls. Takes an OrderIntent plus already-fetched
account/position state and decides whether the order may proceed. The
engine must call evaluate() and get an approval before order_manager ever
touches the broker - this module has no way to submit an order itself.
"""

from dataclasses import dataclass, field
from typing import Optional

from account import AccountSummary, PositionSummary
from logging_setup import log_event
from strategy import OrderIntent


@dataclass
class RiskLimits:
    max_position_size: float  # max shares of one symbol, resulting position
    max_dollar_exposure: float  # max $ value of one symbol's position
    max_portfolio_exposure: float  # max total $ value across all positions
    max_daily_loss: float  # max $ the account may be down today before all new orders are blocked
    max_simultaneous_positions: int  # max distinct symbols held at once
    max_order_size: float  # max $ notional for any single order


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    checks: dict = field(default_factory=dict)
    order_notional: float = 0.0


class RiskManager:
    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def evaluate(
        self,
        intent: OrderIntent,
        current_price: float,
        account: AccountSummary,
        positions: list,
    ) -> RiskDecision:
        checks = {}

        def reject(reason: str) -> RiskDecision:
            decision = RiskDecision(approved=False, reason=reason, checks=checks)
            log_event(
                "risk_rejection",
                symbol=intent.symbol,
                side=intent.side,
                qty=intent.qty,
                reason=reason,
                checks=checks,
            )
            return decision

        # Account-health kill switches take priority over everything else.
        if account.trading_blocked or account.account_blocked:
            checks["account_not_blocked"] = False
            return reject("account is trading_blocked or account_blocked")
        checks["account_not_blocked"] = True

        if account.daily_pl <= -abs(self.limits.max_daily_loss):
            checks["max_daily_loss"] = False
            return reject(
                f"daily P/L {account.daily_pl:.2f} has breached max_daily_loss "
                f"limit of -{self.limits.max_daily_loss:.2f}; all new orders blocked for today"
            )
        checks["max_daily_loss"] = True

        current_price = float(current_price)
        order_notional = intent.qty * (intent.limit_price or current_price)

        # max_order_size caps new capital commitment - it only constrains
        # buys. A sell that reduces/closes an existing position is risk-
        # reducing, not risk-taking, and must never be blocked by it (an
        # exit/stop order must always be able to get out).
        if intent.side == "buy" and order_notional > self.limits.max_order_size:
            checks["max_order_size"] = False
            return reject(
                f"order notional {order_notional:.2f} exceeds max_order_size "
                f"{self.limits.max_order_size:.2f}"
            )
        checks["max_order_size"] = True

        if intent.side == "buy" and order_notional > account.buying_power:
            checks["sufficient_buying_power"] = False
            return reject(
                f"order notional {order_notional:.2f} exceeds available buying "
                f"power {account.buying_power:.2f}"
            )
        checks["sufficient_buying_power"] = True

        existing = next((p for p in positions if p.symbol == intent.symbol), None)
        existing_qty = existing.qty if existing else 0.0
        signed_delta = intent.qty if intent.side == "buy" else -intent.qty
        resulting_qty = existing_qty + signed_delta
        resulting_exposure = abs(resulting_qty) * current_price

        if abs(resulting_qty) > self.limits.max_position_size:
            checks["max_position_size"] = False
            return reject(
                f"resulting position {resulting_qty:.4f} shares of {intent.symbol} "
                f"exceeds max_position_size {self.limits.max_position_size}"
            )
        checks["max_position_size"] = True

        if resulting_exposure > self.limits.max_dollar_exposure:
            checks["max_dollar_exposure"] = False
            return reject(
                f"resulting exposure {resulting_exposure:.2f} on {intent.symbol} "
                f"exceeds max_dollar_exposure {self.limits.max_dollar_exposure:.2f}"
            )
        checks["max_dollar_exposure"] = True

        other_positions_value = sum(
            abs(p.market_value) for p in positions if p.symbol != intent.symbol
        )
        new_portfolio_exposure = other_positions_value + resulting_exposure
        if new_portfolio_exposure > self.limits.max_portfolio_exposure:
            checks["max_portfolio_exposure"] = False
            return reject(
                f"resulting portfolio exposure {new_portfolio_exposure:.2f} exceeds "
                f"max_portfolio_exposure {self.limits.max_portfolio_exposure:.2f}"
            )
        checks["max_portfolio_exposure"] = True

        currently_held_symbols = {p.symbol for p in positions if p.qty != 0}
        opens_new_symbol = existing_qty == 0 and resulting_qty != 0
        resulting_symbol_count = len(currently_held_symbols) + (1 if opens_new_symbol else 0)
        if resulting_symbol_count > self.limits.max_simultaneous_positions:
            checks["max_simultaneous_positions"] = False
            return reject(
                f"opening {intent.symbol} would bring simultaneous positions to "
                f"{resulting_symbol_count}, exceeding max_simultaneous_positions "
                f"{self.limits.max_simultaneous_positions}"
            )
        checks["max_simultaneous_positions"] = True

        decision = RiskDecision(
            approved=True, reason="all checks passed", checks=checks, order_notional=order_notional
        )
        log_event(
            "risk_approval",
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.qty,
            order_notional=order_notional,
            checks=checks,
        )
        return decision
