"""Strategy interface only. No concrete strategy is implemented here.

A strategy is anything that implements Strategy.generate_signal(context) and
returns a Signal, optionally alongside an OrderIntent describing what it
would like to do. The engine is responsible for sending that intent through
risk management before any execution happens - a strategy can never submit
an order itself, it can only recommend one.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Signal(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    NO_ACTION = "NO_ACTION"


@dataclass
class OrderIntent:
    """What a strategy would like to happen. Not an order - has no order id,
    has not been risk-checked, and has not touched the broker API."""

    symbol: str
    side: str  # "buy" or "sell"
    qty: float
    order_type: str = "market"  # "market", "limit", "stop", or "trailing_stop"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trail_percent: Optional[float] = None
    time_in_force: str = "day"
    reason: str = ""


@dataclass
class StrategyContext:
    """Read-only snapshot handed to a strategy so it can decide. Strategies
    receive data, they don't fetch it themselves - keeps strategy logic
    testable without a live API connection."""

    symbol: str
    current_price: float
    historical_bars: list
    volume: int
    market_is_open: bool


class Strategy(ABC):
    """Base class every concrete strategy must implement. Intentionally has
    no subclasses yet - this module only defines the contract."""

    name: str = "unnamed-strategy"

    @abstractmethod
    def generate_signal(self, context: StrategyContext) -> Signal:
        """Return exactly one of Signal.BUY / SELL / HOLD / NO_ACTION."""
        raise NotImplementedError

    @abstractmethod
    def build_order_intent(self, context: StrategyContext, signal: Signal) -> Optional[OrderIntent]:
        """Return an OrderIntent when signal is BUY or SELL, otherwise None.
        Must not talk to the broker - this only describes what is wanted."""
        raise NotImplementedError
