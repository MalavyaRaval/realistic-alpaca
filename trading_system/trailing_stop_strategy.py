"""Configurable paper-trading trailing-stop strategy for a single symbol.

Single-shot lifecycle - no re-entry after exit, no averaging down, no
ladder buying:

  1. No position, no open orders for the symbol -> enter with a market buy
     for `qty` shares, gated by the full risk-manager approval (the same
     engine.process_signal path every other signal uses).
  2. Position exists, no protective order resting -> place the initial
     fixed stop, calculated from the ACTUAL fill price (Alpaca's reported
     avg_entry_price on the live position), not the price we intended to
     pay.
  3. Position exists, a fixed stop is resting, and price has reached
     +trailing_activation_pct above avg_entry_price -> cancel the fixed
     stop and replace it with a native Alpaca TRAILING_STOP sell order at
     trailing_distance_pct. Alpaca's own trailing-stop order type ratchets
     the stop up as price rises and cannot move down - we never recompute
     or replace it ourselves once it exists, so there is no code path that
     could push it down.
  4. Position exists, a trailing stop is already resting -> nothing to do;
     the broker manages it and converts it to a market sell if touched.
  5. Position is flat again after previously being open -> done,
     permanently. This strategy never re-enters and never adds to a
     losing position.

Every call to run_cycle() re-fetches the live position and open orders
before deciding anything (reconciliation) - nothing about entry price,
fill status, or protective-order state is trusted from memory.
"""

from dataclasses import dataclass
from typing import Optional

import account
import engine
import market_data
import order_manager
from logging_setup import log_event
from risk_manager import RiskManager
from strategy import OrderIntent, Signal, Strategy, StrategyContext

# Protective stops must survive across sessions, not expire at end of day.
PROTECTIVE_TIME_IN_FORCE = "gtc"


@dataclass
class TrailingStopParams:
    symbol: str = "TSLA"
    qty: float = 10
    initial_stop_pct: float = 0.10  # 10% below actual fill price
    trailing_activation_pct: float = 0.10  # +10% from avg entry activates trailing
    trailing_distance_pct: float = 0.05  # 5% below highest price after activation


@dataclass
class CycleResult:
    action: str
    detail: str
    order_result: Optional[object] = None


class TrailingStopStrategy(Strategy):
    name = "trailing-stop"

    def __init__(self, params: TrailingStopParams):
        self.params = params
        self._done = False  # once True, never re-enters

    # --- Strategy interface: the entry decision only. This is what makes
    # the entry go through engine.process_signal exactly like any other
    # signal - the strategy has no way to submit an order itself. ---

    def generate_signal(self, context: StrategyContext) -> Signal:
        if self._done:
            return Signal.NO_ACTION
        return Signal.BUY

    def build_order_intent(self, context: StrategyContext, signal: Signal) -> Optional[OrderIntent]:
        if signal != Signal.BUY:
            return None
        return OrderIntent(
            symbol=self.params.symbol, side="buy", qty=self.params.qty,
            order_type="market", time_in_force="day",
            reason="trailing-stop strategy initial entry",
        )

    # --- Full reconciliation cycle. Entry goes through the risk gate;
    # protective-stop placement/upgrade is exit-side position management on
    # a position we already legitimately hold, so it talks to
    # order_manager directly rather than re-running the buy-side risk
    # gate (an exit must always be able to happen). ---

    def run_cycle(self, risk_mgr: RiskManager) -> CycleResult:
        symbol = self.params.symbol

        positions = account.get_positions()
        open_orders = account.get_open_orders()
        position = next((p for p in positions if p.symbol == symbol), None)
        symbol_orders = [o for o in open_orders if o.symbol == symbol]

        if position is None or position.qty == 0:
            pending_entry = [o for o in symbol_orders if o.side == "buy"]
            if pending_entry:
                return CycleResult(
                    "waiting", f"entry order {pending_entry[0].id} still open, not filled yet"
                )

            if self._done:
                return CycleResult("no_action", "strategy already completed; no re-entry")

            # A filled sell order can't be seen in open_orders any more (it's
            # no longer open) - open-orders alone can't distinguish "never
            # traded" from "already completed a cycle". Check closed-order
            # history instead so this survives a process restart, not just
            # the in-memory self._done flag.
            if self._already_completed_a_cycle():
                self._done = True
                return CycleResult(
                    "exited",
                    "a previous entry for this symbol was already filled and the "
                    "position is now flat; strategy complete, no re-entry",
                )

            return self._enter(risk_mgr)

        return self._manage_protective_order(position, symbol_orders)

    def _already_completed_a_cycle(self) -> bool:
        filled_entries = account.get_closed_orders(self.params.symbol, side="buy")
        return any(o.status == "filled" for o in filled_entries)

    def _enter(self, risk_mgr: RiskManager) -> CycleResult:
        context = StrategyContext(
            symbol=self.params.symbol,
            current_price=market_data.get_current_price(self.params.symbol),
            historical_bars=[],
            volume=market_data.get_volume(self.params.symbol),
            market_is_open=market_data.is_market_open(),
        )
        signal = self.generate_signal(context)
        intent = self.build_order_intent(context, signal)
        result = engine.process_signal(self.name, signal, intent, risk_mgr)

        if not result.approved:
            return CycleResult("entry_blocked", result.reason)
        if result.order_result is None or not result.order_result.accepted:
            return CycleResult("entry_failed", result.reason)

        log_event(
            "strategy", strategy=self.name, action="entry_submitted",
            order_id=result.order_result.order_id, symbol=self.params.symbol,
        )
        return CycleResult(
            "entry_submitted",
            f"order {result.order_result.order_id} submitted, awaiting fill",
            result.order_result,
        )

    def _manage_protective_order(self, position, symbol_orders) -> CycleResult:
        stop_orders = [
            o for o in symbol_orders
            if o.side == "sell" and o.order_type in ("stop", "trailing_stop")
        ]

        avg_entry = position.avg_entry_price
        current_price = market_data.get_current_price(self.params.symbol)
        activation_price = avg_entry * (1 + self.params.trailing_activation_pct)
        activated = current_price >= activation_price

        if not stop_orders:
            # Nothing resting - place one now. Skip straight to trailing if
            # we're already past the activation threshold (e.g. this is
            # the first reconciliation after a restart and price already
            # ran up while nothing was watching).
            if activated:
                return self._place_trailing_stop(position)
            return self._place_initial_stop(position)

        stop_order = stop_orders[0]
        if stop_order.order_type == "trailing_stop":
            return CycleResult(
                "holding", f"trailing stop {stop_order.id} already active; broker manages it"
            )

        if activated:
            cancelled = order_manager.cancel_order(stop_order.id)
            if not cancelled:
                return CycleResult(
                    "upgrade_failed",
                    f"could not cancel fixed stop {stop_order.id} before upgrading to trailing",
                )
            return self._place_trailing_stop(position)

        return CycleResult(
            "holding",
            f"fixed stop {stop_order.id} active; price {current_price:.2f}, "
            f"activation at {activation_price:.2f}",
        )

    def _place_initial_stop(self, position) -> CycleResult:
        fill_price = position.avg_entry_price  # actual fill price, reconciled from the broker
        stop_price = round(fill_price * (1 - self.params.initial_stop_pct), 2)
        intent = OrderIntent(
            symbol=self.params.symbol, side="sell", qty=abs(position.qty),
            order_type="stop", stop_price=stop_price, time_in_force=PROTECTIVE_TIME_IN_FORCE,
            reason=f"initial stop, {self.params.initial_stop_pct:.0%} below fill price {fill_price}",
        )
        result = order_manager.submit_order(intent)
        log_event(
            "strategy", strategy=self.name, action="initial_stop_placed",
            order_id=result.order_id, symbol=self.params.symbol, stop_price=stop_price,
        )
        return CycleResult(
            "initial_stop_placed" if result.accepted else "initial_stop_failed",
            result.reason or f"initial stop placed at {stop_price} (order {result.order_id})",
            result,
        )

    def _place_trailing_stop(self, position) -> CycleResult:
        intent = OrderIntent(
            symbol=self.params.symbol, side="sell", qty=abs(position.qty),
            order_type="trailing_stop",
            trail_percent=self.params.trailing_distance_pct * 100,
            time_in_force=PROTECTIVE_TIME_IN_FORCE,
            reason=f"trailing stop activated, {self.params.trailing_distance_pct:.0%} trail",
        )
        result = order_manager.submit_order(intent)
        log_event(
            "strategy", strategy=self.name, action="trailing_stop_activated",
            order_id=result.order_id, symbol=self.params.symbol,
        )
        return CycleResult(
            "trailing_stop_activated" if result.accepted else "trailing_stop_failed",
            result.reason or f"trailing stop activated (order {result.order_id})",
            result,
        )
