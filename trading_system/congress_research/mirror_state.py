"""Persisted state for the congress-copy strategy - designed to be
reloaded fresh on every invocation, not kept in a long-running process's
memory.

`open_position_tickers` replaces the earlier single `current_position_
ticker` field now that the strategy can hold multiple simultaneous
copied positions (up to max_positions). Old state files from the prior
single-position version simply don't have this key, so DEFAULT_STATE
supplies an empty list for them - no separate migration step needed
since a single-position deployment never had more than one open ticker
to carry forward anyway.
"""

import json
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent / "data" / "mirror_state.json"

DEFAULT_STATE = {
    "halted": False,
    "halt_reason": None,
    "open_position_tickers": [],
    "seen_transaction_ids": [],
    "last_run_at": None,
}


def load_state() -> dict:
    if not STATE_PATH.exists():
        return dict(DEFAULT_STATE)
    with STATE_PATH.open(encoding="utf-8") as f:
        state = json.load(f)
    merged = {**DEFAULT_STATE, **state}
    merged["open_position_tickers"] = list(merged.get("open_position_tickers") or [])
    merged["seen_transaction_ids"] = list(merged.get("seen_transaction_ids") or [])
    return merged


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
