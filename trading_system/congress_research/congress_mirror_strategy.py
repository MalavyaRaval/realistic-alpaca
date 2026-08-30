"""Congress-mirror strategy: turns a disclosed congressional stock
PURCHASE into a paper-trading BUY signal, then hands off to the same
trailing-stop entry/exit machinery already built and tested for ABEV.

What this is and isn't:
  - This mirrors a PUBLICLY DISCLOSED, ALREADY-STALE signal. Every
    candidate has a real disclosure_age_days - the trade already happened
    days to months ago. This is not, and cannot be, a fast-reaction
    strategy; it is a systematic, rules-based response to old public
    information, not a claim that the timing is somehow still an edge.
  - No trade is ever selected, weighted, or excluded based on any
    politician's past trading outcomes, party, or reputation. The only
    filters are: transaction_type == 'Purchase', a real ticker, an
    allowed chamber, and "we haven't acted on this specific disclosure
    yet". Nothing here asserts or implies a trade was made using material
    nonpublic information, and nothing assumes a politician's past
    results predict this one.
  - Senate eFD data is EXCLUDED from this strategy's candidates by
    default (allowed_chambers=("house",)). The Senate eFD system's own
    disclaimer states its reports may not be used for a commercial
    purpose other than news/media dissemination (5 U.S.C. app. §105(c)),
    with civil penalties for violators - a restriction the congress
    research module's docstrings already flag. Only pass
    allowed_chambers including "senate" if you've made your own judgment
    about that restriction; this module defaults to the conservative
    reading.
  - Single position at a time, sized tiny (1 share by default) - this
    account has ~$50-60 of buying power, so most candidates will simply
    be rejected by the risk gate for insufficient funds. That's the risk
    manager working correctly, not a bug to route around.
"""

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database
import mirror_state
from risk_manager import RiskManager
from trailing_stop_strategy import CycleResult, TrailingStopParams, TrailingStopStrategy


@dataclass
class CongressMirrorParams:
    qty: float = 1
    initial_stop_pct: float = 0.10
    trailing_activation_pct: float = 0.10
    trailing_distance_pct: float = 0.05
    max_candidates_per_cycle: int = 5
    allowed_chambers: tuple = ("house",)  # see module docstring re: Senate eFD restriction


class CongressMirrorStrategy:
    name = "congress-mirror"

    def __init__(self, params: CongressMirrorParams):
        self.params = params

    def _sub_strategy(self, ticker: str) -> TrailingStopStrategy:
        return TrailingStopStrategy(TrailingStopParams(
            symbol=ticker,
            qty=self.params.qty,
            initial_stop_pct=self.params.initial_stop_pct,
            trailing_activation_pct=self.params.trailing_activation_pct,
            trailing_distance_pct=self.params.trailing_distance_pct,
        ))

    def run_cycle(self, risk_mgr: RiskManager) -> CycleResult:
        state = mirror_state.load_state()
        state["last_run_at"] = datetime.now(timezone.utc).isoformat()

        if state["halted"]:
            return CycleResult("halted_skip", f"congress-mirror is halted: {state['halt_reason']}")

        try:
            if state["current_position_ticker"]:
                ticker = state["current_position_ticker"]
                result = self._sub_strategy(ticker).run_cycle(risk_mgr)
                if result.action == "exited":
                    state["current_position_ticker"] = None
                mirror_state.save_state(state)
                return CycleResult(result.action, f"[{ticker}] {result.detail}")

            candidates = database.get_new_purchase_candidates(
                seen_ids=state["seen_transaction_ids"],
                chambers=self.params.allowed_chambers,
                limit=self.params.max_candidates_per_cycle,
            )
            if not candidates:
                mirror_state.save_state(state)
                return CycleResult("no_action", "no new qualifying disclosures to consider")

            for candidate in candidates:
                mirror_state.mark_seen(state, candidate.id)
                result = self._sub_strategy(candidate.ticker).run_cycle(risk_mgr)
                mirror_state.save_state(state)

                if result.action == "entry_submitted":
                    state["current_position_ticker"] = candidate.ticker
                    mirror_state.save_state(state)
                    return CycleResult(
                        "entry_submitted",
                        f"mirroring {candidate.politician_name}'s ({candidate.chamber}) purchase of "
                        f"{candidate.ticker}: traded {candidate.transaction_date}, disclosed "
                        f"{candidate.disclosure_date} ({candidate.disclosure_age_days}d later). {result.detail}",
                    )
                # else: blocked/failed (usually insufficient funds) - try the next candidate

            mirror_state.save_state(state)
            return CycleResult(
                "no_action",
                f"considered {len(candidates)} new disclosure(s), none could be entered "
                f"(most likely: insufficient buying power)",
            )

        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            mirror_state.set_halted(state, reason)
            mirror_state.save_state(state)
            return CycleResult("halted", f"unexpected condition, halting: {reason}")
