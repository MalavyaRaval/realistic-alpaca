"""Structured, append-only event log for the trading framework.

Every market-data lookup, strategy decision, order action, fill, cancellation,
error, and risk decision must go through log_event() so the full history of
what the system saw and did is reconstructable after the fact.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
EVENTS_PATH = LOG_DIR / "events.jsonl"

VALID_CATEGORIES = {
    "market_data",
    "strategy",
    "order",
    "fill",
    "cancellation",
    "error",
    "risk_rejection",
    "risk_approval",
    "monitor",
}

_lock = threading.Lock()


def log_event(category: str, **fields) -> dict:
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"Unknown log category {category!r}; must be one of {sorted(VALID_CATEGORIES)}"
        )
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "category": category,
        **fields,
    }
    line = json.dumps(record, default=str)
    with _lock:
        with EVENTS_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    print(f"[{record['timestamp']}] {category}: {fields}")
    return record


def read_events(category: str = None) -> list:
    """Reads back the event log, optionally filtered by category. Used by
    tests and by anyone auditing what the system did."""
    if not EVENTS_PATH.exists():
        return []
    events = []
    with EVENTS_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if category is None or record.get("category") == category:
                events.append(record)
    return events
