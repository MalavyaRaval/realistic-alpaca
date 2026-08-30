"""Persisted state for the congress-mirror strategy - same pattern as
monitor_state.py in the main trading_system: designed to be reloaded
fresh on every invocation, not kept in a long-running process's memory.
"""

import json
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent / "data" / "mirror_state.json"

DEFAULT_STATE = {
    "halted": False,
    "halt_reason": None,
    "current_position_ticker": None,
    "seen_transaction_ids": [],
    "last_run_at": None,
}


def load_state() -> dict:
    if not STATE_PATH.exists():
        return dict(DEFAULT_STATE)
    with STATE_PATH.open(encoding="utf-8") as f:
        state = json.load(f)
    return {**DEFAULT_STATE, **state}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)


def set_halted(state: dict, reason: str) -> None:
    state["halted"] = True
    state["halt_reason"] = reason


def clear_halt() -> None:
    """Deliberate, human-invoked action after investigating a halt -
    nothing in the strategy itself ever calls this."""
    state = load_state()
    state["halted"] = False
    state["halt_reason"] = None
    save_state(state)


def mark_seen(state: dict, transaction_id: str) -> None:
    if transaction_id not in state["seen_transaction_ids"]:
        state["seen_transaction_ids"].append(transaction_id)
