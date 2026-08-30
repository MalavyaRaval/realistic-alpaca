"""Persisted state for the monitoring job.

The monitor is designed to be invoked fresh every 5 minutes by an external
scheduler (cron, Task Scheduler, etc) - nothing survives in memory between
invocations. The halted flag and the ratcheting "highest price observed" /
"last stop level" values are the only things that need to persist, and they
live here as a small JSON file, not in a running process.
"""

import json
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent / "logs" / "monitor_state.json"

DEFAULT_SYMBOL_STATE = {
    "halted": False,
    "halt_reason": None,
    "highest_price_observed": None,
    "last_stop_level": None,
    "last_run_at": None,
}


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    with STATE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(exist_ok=True)
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)


def get_symbol_state(state: dict, symbol: str) -> dict:
    return {**DEFAULT_SYMBOL_STATE, **state.get(symbol, {})}


def update_symbol_state(state: dict, symbol: str, **fields) -> None:
    sym_state = state.setdefault(symbol, dict(DEFAULT_SYMBOL_STATE))
    sym_state.update(fields)


def set_halted(state: dict, symbol: str, reason: str) -> None:
    sym_state = state.setdefault(symbol, dict(DEFAULT_SYMBOL_STATE))
    sym_state["halted"] = True
    sym_state["halt_reason"] = reason


def clear_halt(symbol: str) -> None:
    """Deliberate, human-invoked action taken after investigating a halt.
    Nothing in the monitor itself ever calls this - resuming requires a
    person to decide the issue is actually resolved."""
    state = load_state()
    sym_state = state.setdefault(symbol, dict(DEFAULT_SYMBOL_STATE))
    sym_state["halted"] = False
    sym_state["halt_reason"] = None
    save_state(state)
