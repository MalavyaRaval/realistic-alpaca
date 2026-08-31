"""Congress-copy strategy: turns disclosed congressional stock PURCHASES
into paper-trading BUY signals, gated by a full validation pipeline
before any trade is even considered, then handed off to the same
trailing-stop entry/exit machinery already built and tested for ABEV.

This does NOT blindly copy every disclosed transaction. Before a
candidate can become a trade, decide_candidate() checks, IN ORDER:
  1. Not already holding a copied position in this ticker, and not
     already at max_positions.
  2. The transaction's ORIGINAL SOURCE DOCUMENT verifies (source_
     verification.py) - never trust the local database cache alone.
  3. Disclosure age (days between transaction and disclosure, already
     computed and stored per transaction) is within
     max_disclosure_age_days - a transaction disclosed too late is
     rejected as stale.
  4. The disclosed asset type is on a confirmed-plain-equity allowlist
     (broker_checks.py) - options and anything not confidently
     identifiable as ordinary common stock are rejected. This module has
     no options execution path at all, regardless of allow_options.
  5. The ticker is checked against the connected brokerage directly
     (broker_checks.py) - not assumed tradable just because it has a
     ticker in the database, and not assumed equity just because the
     disclosure says so (the broker's own asset_class is also checked).
  6. Position size is computed from three independent dollar caps -
     max_single_security_pct of equity, the remaining room under
     max_strategy_pct of equity across all currently-open copied
     positions (filled AND still-resting-unfilled combined - see
     pending_notional below), and available cash. The cash cap itself is
     min(account.cash, account.buying_power): cash never grows with
     margin, and buying_power is what the broker itself is currently
     willing to commit (which already reflects its own hold on any
     resting order) - taking the smaller of the two is what makes "never
     use margin, never use leverage, never spend more than available"
     hold regardless of which one a given account setup makes tighter.
     Whichever cap is smallest wins; if it rounds to zero shares, reject.

Every candidate this strategy looks at - approved or rejected - is
logged with its specific reason (see run_cycle()'s log_event calls).

Two structural safety nets exist ALONGSIDE the gates above, added after
an adversarial audit of the multi-position/multi-cycle design:
  - pending_notional (computed once per cycle in run_cycle(), from this
    strategy's own still-resting BUY orders) is added to already_deployed
    in decide_candidate(), so a new candidate's sizing can't ignore a
    same-strategy order that hasn't filled yet. If that pending notional
    can't be safely computed (a price lookup fails), new candidates are
    rejected for the whole cycle rather than sized against an
    understated, possibly-wrong budget - existing position management is
    unaffected.
  - The reconciliation loop over open_position_tickers NEVER lets
    TrailingStopStrategy submit a fresh entry: it's always called with a
    dedicated no-entry RiskManager (max_order_size=0 and friends) built
    by THIS module, not borrowed from whatever the caller happens to
    pass in. That way, an ambiguous state - e.g. a tracked ticker's day
    order expiring between this loop's own snapshot and
    TrailingStopStrategy's fresh internal re-check - can only ever
    surface as a blocked, logged, retried-next-cycle no-op, never as an
    ungated real order. The one-time cost is that a ticker whose entry
    genuinely never fills stays tracked (consuming a max_positions slot)
    rather than being cleaned up - a bounded, low-impact tradeoff against
    ever letting an entry slip past decide_candidate()'s gates.
  - Before doing anything else, run_cycle() reconciles the OTHER
    direction too: any ticker with a real, live broker position that
    ISN'T in open_position_tickers (e.g. this process crashed between
    submitting an order and persisting the tracking list) is added back
    in immediately, so it gets picked up by the same management loop
    this cycle rather than being permanently invisible to every future
    one.
  - A single ticker's management raising an exception no longer drops
    the whole cycle's reconciliation for every OTHER tracked ticker (and
    hence doesn't halt protective-stop management account-wide over one
    ticker's transient data hiccup) - each ticker is handled in its own
    try/except, logged and retried next cycle on failure, with the
    ticker staying conservatively tracked.

Nothing here asserts or implies a disclosed transaction used material
nonpublic information, and nothing assumes a politician's past results
predict this one - this module only decides whether a disclosed, already
-public transaction is safe and eligible to mirror under explicit,
configured limits.

dry_run mode (see CongressCopyStrategy.__init__, default True): lets the
strategy be scheduled and actively watching for new disclosures before a
single real order has ever been shown to and confirmed by a human. When
a candidate passes every gate, the exact proposed trade is reported
instead of submitted, and the transaction is deliberately NOT marked
seen - so it resurfaces unchanged the first time this runs with
dry_run=False, guaranteeing what a human confirms is exactly what then
executes.
"""

import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import account
import database
import market_data
import mirror_state
from broker_checks import check_tradable, is_confirmed_equity, is_options_disclosure
from config import get_trading_client
from logging_setup import log_event
from risk_manager import RiskLimits, RiskManager
from source_verification import verify_source_document
from trailing_stop_strategy import CycleResult, TrailingStopParams, TrailingStopStrategy


@dataclass
class CongressCopyParams:
    max_single_security_pct: float = 0.05   # of portfolio equity
    max_strategy_pct: float = 0.20          # of portfolio equity, aggregate across all copied positions
    max_positions: int = 10
    max_disclosure_age_days: int = 45       # the STOCK Act's own filing deadline
    allow_options: bool = False             # this module has no options execution path regardless
    initial_stop_pct: float = 0.10
    trailing_activation_pct: float = 0.10
    trailing_distance_pct: float = 0.05
    max_candidates_per_cycle: int = 10
    # House-only by default for two reasons: Senate eFD source documents
    # can only be weakly verified (see source_verification.py's
    # docstring), and Senate eFD data carries a statutory use restriction
    # (5 U.S.C. app. section 105(c)) against commercial use beyond news/
    # media dissemination (see database.py's docstring for the full text).
    allowed_chambers: tuple = ("house",)


@dataclass
class CandidateDecision:
    transaction_id: str
    ticker: str
    politician_name: str
    approved: bool
    reason: str
    qty: Optional[int] = None
    dollar_amount: Optional[float] = None


class CongressCopyStrategy:
    name = "congress-copy"

    def __init__(self, params: CongressCopyParams, dry_run: bool = True):
        """dry_run (default True): when a candidate passes every gate,
        report the exact trade that WOULD be submitted instead of
        actually submitting it, and don't mark that transaction seen -
        so the identical candidate resurfaces, unchanged, the first time
        this runs with dry_run=False. This is what lets the strategy be
        run on a schedule (so it's actually watching for new disclosures)
        before a single real order has ever been shown to and confirmed
        by a human, per this project's standing requirement that the
        first copied trade be confirmed before it happens. Existing
        positions are still fully managed regardless of dry_run, since
        dry_run only ever affects whether a NEW entry gets submitted."""
        self.params = params
        self.dry_run = dry_run

    def _sub_strategy(self, ticker: str, qty: float) -> TrailingStopStrategy:
        return TrailingStopStrategy(TrailingStopParams(
            symbol=ticker, qty=qty,
            initial_stop_pct=self.params.initial_stop_pct,
            trailing_activation_pct=self.params.trailing_activation_pct,
            trailing_distance_pct=self.params.trailing_distance_pct,
        ))

    def decide_candidate(
        self, candidate, acct, positions: list, open_position_tickers: list,
        pending_notional: Optional[float],
    ) -> CandidateDecision:
        """Runs every validation gate for one candidate and returns a
        fully-reasoned decision, WITHOUT submitting anything - run_cycle()
        and the preview script share this exact method, so a preview is
        guaranteed to match what a live cycle would actually decide.

        pending_notional is this strategy's own still-resting (unfilled)
        BUY order dollar total, computed once per cycle by run_cycle() -
        pass None if it couldn't be safely computed, which rejects every
        candidate this cycle rather than sizing against an understated
        budget."""

        def decision(approved: bool, reason: str, **kw) -> CandidateDecision:
            return CandidateDecision(
                transaction_id=candidate.id, ticker=candidate.ticker,
                politician_name=candidate.politician_name, approved=approved, reason=reason, **kw,
            )

        if len(open_position_tickers) >= self.params.max_positions:
            return decision(False, f"already at max_positions ({self.params.max_positions})")

        if candidate.ticker in open_position_tickers:
            return decision(False, f"already holding a copied position in {candidate.ticker}")

        verified, verify_reason = verify_source_document(candidate.source_document_url)
        if not verified:
            return decision(False, f"source verification failed: {verify_reason}")

        if candidate.disclosure_age_days is None:
            return decision(False, "disclosure age could not be determined - transaction or disclosure date missing")
        if candidate.disclosure_age_days > self.params.max_disclosure_age_days:
            return decision(False, (
                f"stale: disclosed {candidate.disclosure_age_days}d after the trade, exceeds the "
                f"configured maximum of {self.params.max_disclosure_age_days}d"
            ))

        if not is_confirmed_equity(candidate.asset_type):
            if is_options_disclosure(candidate.asset_type):
                return decision(False, f"options disclosure (asset_type={candidate.asset_type!r}) - options are not traded by this strategy")
            return decision(False, f"asset_type {candidate.asset_type!r} is not a confirmed plain-equity type - skipped rather than assumed safe")

        client = get_trading_client()
        tradable, tradable_reason = check_tradable(client, candidate.ticker)
        if not tradable:
            return decision(False, f"not tradable through the connected brokerage: {tradable_reason}")

        if pending_notional is None:
            return decision(False, "cannot verify this strategy's own pending order exposure this cycle - rejecting rather than sizing against an unknown budget")

        try:
            price = market_data.get_current_price(candidate.ticker)
        except market_data.MarketDataUnavailable as e:
            return decision(False, f"could not price {candidate.ticker}: {e}")

        max_by_security = acct.equity * self.params.max_single_security_pct
        already_deployed = sum(
            p.market_value for p in positions if p.symbol in open_position_tickers
        ) + pending_notional
        max_by_strategy = max(0.0, acct.equity * self.params.max_strategy_pct - already_deployed)
        # Never margin, never leverage, never more than the broker will
        # actually commit: cash never grows with margin, and buying_power
        # is the broker's own live figure (already discounted for any
        # resting order it's holding funds against) - the smaller of the
        # two is what's actually safe to commit to a NEW order.
        max_by_cash = min(acct.cash, acct.buying_power)

        max_dollars = min(max_by_security, max_by_strategy, max_by_cash)
        qty = math.floor(max_dollars / price) if price > 0 else 0
        if qty < 1:
            return decision(False, (
                f"position size rounds to 0 shares at ${price:.2f}/share under current limits "
                f"(security cap ${max_by_security:.2f}, strategy cap ${max_by_strategy:.2f}, cash/buying-power cap ${max_by_cash:.2f})"
            ))

        dollar_amount = qty * price
        return decision(True, (
            f"approved: {qty} share(s) of {candidate.ticker} (~${dollar_amount:.2f}) within "
            f"security (${max_by_security:.2f}), strategy (${max_by_strategy:.2f}, already ${already_deployed:.2f} "
            f"deployed/pending), and cash/buying-power (${max_by_cash:.2f}) limits"
        ), qty=qty, dollar_amount=dollar_amount)

    def _entry_risk_manager(self, decision: CandidateDecision, acct, outer_risk_mgr: RiskManager) -> RiskManager:
        """A fresh RiskManager reflecting THIS specific candidate's
        already-computed dynamic dollar caps, so the shared risk gate
        (engine.process_signal -> risk_manager.evaluate) enforces the
        same limits decide_candidate() already decided, not a separate,
        possibly-looser static one. max_daily_loss is carried over from
        the caller's outer risk manager, since that circuit breaker is
        an account-wide setting, not a per-candidate one."""
        return RiskManager(RiskLimits(
            max_position_size=decision.qty,
            max_dollar_exposure=decision.dollar_amount,
            max_portfolio_exposure=acct.equity * self.params.max_strategy_pct,
            max_order_size=decision.dollar_amount,
            max_simultaneous_positions=self.params.max_positions,
            max_daily_loss=outer_risk_mgr.limits.max_daily_loss,
        ))

    def _no_entry_risk_manager(self, outer_risk_mgr: RiskManager) -> RiskManager:
        """Used ONLY for the reconciliation loop over already-tracked
        tickers - structurally incapable of approving a fresh entry
        (max_order_size=0 and friends) regardless of what the caller's
        own risk_mgr allows, so an already-tracked ticker whose state is
        momentarily ambiguous (see the module docstring) can never reach
        a real, ungated BUY order through this loop. Exit-side protective
        -stop management doesn't go through this risk gate at all (see
        TrailingStopStrategy), so this has no effect on managing a
        position that's genuinely still open."""
        return RiskManager(RiskLimits(
            max_position_size=0, max_dollar_exposure=0, max_portfolio_exposure=0,
            max_order_size=0, max_simultaneous_positions=0,
            max_daily_loss=outer_risk_mgr.limits.max_daily_loss,
        ))

    def _pending_buy_notional(self, open_orders: list, tracked_tickers: list) -> Optional[float]:
        """Dollar total of this strategy's own still-resting BUY orders
        for currently-tracked tickers, priced at current market price
        (the closest available estimate for an unfilled market order).
        Returns None if any needed price can't be fetched - callers must
        treat that as "unknown, assume worst case" rather than silently
        undercounting it."""
        total = 0.0
        for o in open_orders:
            if o.side != "buy" or o.symbol not in tracked_tickers:
                continue
            try:
                total += o.qty * market_data.get_current_price(o.symbol)
            except market_data.MarketDataUnavailable:
                return None
        return total

    def run_cycle(self, risk_mgr: RiskManager) -> CycleResult:
        state = mirror_state.load_state()
        state["last_run_at"] = datetime.now(timezone.utc).isoformat()

        if state["halted"]:
            return CycleResult("halted_skip", f"congress-copy is halted: {state['halt_reason']}")

        try:
            positions = account.get_positions()
            open_orders = account.get_open_orders()

            # Recover a real, live position that isn't in our tracked
            # list (e.g. a crash between order submission and persisting
            # open_position_tickers) BEFORE managing anything, so it's
            # picked up by this very cycle's management loop instead of
            # staying permanently invisible.
            tracked = set(state["open_position_tickers"])
            for p in positions:
                if p.symbol not in tracked and p.qty != 0:
                    state["open_position_tickers"].append(p.symbol)
                    log_event(
                        "error", source="congress_mirror_strategy.run_cycle", ticker=p.symbol,
                        message="recovered an untracked live position, likely from a prior crash between "
                                "order submission and state persistence; added back to tracking",
                    )

            no_entry_risk_mgr = self._no_entry_risk_manager(risk_mgr)
            still_open = []
            exit_notes = []
            for ticker in state["open_position_tickers"]:
                try:
                    result = self._sub_strategy(ticker, qty=1).run_cycle(no_entry_risk_mgr)
                except Exception as e:
                    # One ticker's transient failure must not cost the
                    # whole cycle's reconciliation for every OTHER
                    # tracked ticker - keep it tracked (safe default:
                    # retry next cycle) rather than losing already-
                    # decided exits for unrelated tickers or halting
                    # protective-stop management account-wide.
                    still_open.append(ticker)
                    exit_notes.append(f"[{ticker}] error this cycle, will retry next cycle: {e}")
                    log_event("error", source="congress_mirror_strategy.run_cycle", ticker=ticker, message=str(e))
                    continue
                if result.action != "exited":
                    still_open.append(ticker)
                exit_notes.append(f"[{ticker}] {result.action}: {result.detail}")
            state["open_position_tickers"] = still_open
            mirror_state.save_state(state)

            acct = account.get_account_summary()
            pending_notional = self._pending_buy_notional(open_orders, state["open_position_tickers"])

            candidates = database.get_new_purchase_candidates(
                seen_ids=state["seen_transaction_ids"],
                chambers=self.params.allowed_chambers,
                limit=self.params.max_candidates_per_cycle,
            )

            for candidate in candidates:
                dec = self.decide_candidate(candidate, acct, positions, state["open_position_tickers"], pending_notional)
                log_event(
                    "strategy", strategy=self.name, transaction_id=dec.transaction_id,
                    ticker=dec.ticker, politician=dec.politician_name,
                    approved=dec.approved, reason=dec.reason, qty=dec.qty, dollar_amount=dec.dollar_amount,
                    dry_run=self.dry_run,
                )

                if dec.approved and self.dry_run:
                    # Deliberately do NOT mark this transaction seen -
                    # this exact candidate must resurface, unchanged, the
                    # first time this runs with dry_run=False, so what a
                    # human confirms is exactly what then executes.
                    mirror_state.save_state(state)
                    return CycleResult(
                        "dry_run_proposal",
                        f"DRY RUN - would copy {candidate.politician_name}'s purchase of {candidate.ticker}: "
                        f"{dec.reason}. NO ORDER WAS SUBMITTED (dry_run=True). Show this to the user and get "
                        f"explicit confirmation before re-running with dry_run=False.",
                    )

                mirror_state.mark_seen(state, candidate.id)
                if not dec.approved:
                    continue

                entry_risk_mgr = self._entry_risk_manager(dec, acct, risk_mgr)
                sub = self._sub_strategy(candidate.ticker, qty=dec.qty)
                result = sub.run_cycle(entry_risk_mgr)
                mirror_state.save_state(state)

                if result.action == "entry_submitted":
                    state["open_position_tickers"].append(candidate.ticker)
                    mirror_state.save_state(state)
                    return CycleResult(
                        "entry_submitted",
                        f"copied {candidate.politician_name}'s purchase of {candidate.ticker}: {dec.reason}. {result.detail}",
                    )
                # engine-level rejection (e.g. a race on buying power) after our own gates passed - try the next candidate

            mirror_state.save_state(state)
            summary = "; ".join(exit_notes) if exit_notes else "no open positions to manage"
            if candidates:
                return CycleResult("no_action", f"considered {len(candidates)} new disclosure(s), none resulted in a trade. {summary}")
            return CycleResult("no_action", f"no new qualifying disclosures to consider. {summary}")

        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            mirror_state.set_halted(state, reason)
            mirror_state.save_state(state)
            log_event("error", source="congress_mirror_strategy.run_cycle", message=reason)
            return CycleResult("halted", f"unexpected condition, halting: {reason}")
