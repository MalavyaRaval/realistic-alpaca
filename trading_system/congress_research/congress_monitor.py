"""Market-hours-gating wrapper for CongressMirrorStrategy, mirroring the
safety pattern in the main trading_system's monitor.py.

CongressMirrorStrategy.run_cycle() delegates straight into
TrailingStopStrategy's entry logic, which does NOT itself check market
hours (ABEV's monitor.py provides that gate at a higher level). Congress-
mirror needs its own equivalent gate since it doesn't have one fixed
symbol the way ABEV's monitor does - without this wrapper, invoking the
strategy directly could submit a real (paper) DAY order that queues for
the next open, bypassing the "no trading outside market hours" rule.
"""

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import market_data
import mirror_state
from logging_setup import log_event


@dataclass
class CongressMonitorResult:
    timestamp: str
    action: str
    reason: str


def run_congress_mirror_cycle(strategy, risk_mgr) -> CongressMonitorResult:
    timestamp = datetime.now(timezone.utc).isoformat()
    state = mirror_state.load_state()

    if state["halted"]:
        result = CongressMonitorResult(timestamp, "halted_skip", f"halted: {state['halt_reason']}")
        log_event("monitor", strategy="congress-mirror", timestamp=timestamp,
                   action=result.action, reason=result.reason)
        return result

    try:
        market_open = market_data.is_market_open()
    except market_data.MarketDataUnavailable as e:
        mirror_state.set_halted(state, f"market clock unavailable: {e}")
        mirror_state.save_state(state)
        result = CongressMonitorResult(timestamp, "halted", f"market clock unavailable, halting: {e}")
        log_event("error", source="congress_monitor.run_congress_mirror_cycle", message=str(e))
        return result

    if not market_open:
        result = CongressMonitorResult(timestamp, "no_action", "market closed; no trading")
        log_event("monitor", strategy="congress-mirror", timestamp=timestamp,
                   action=result.action, reason=result.reason)
        return result

    cycle_result = strategy.run_cycle(risk_mgr)
    result = CongressMonitorResult(timestamp, cycle_result.action, cycle_result.detail)
    log_event("monitor", strategy="congress-mirror", timestamp=timestamp,
               action=result.action, reason=result.reason)
    return result
