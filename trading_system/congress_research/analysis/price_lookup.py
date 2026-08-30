"""Point-in-time-safe price lookups.

The one rule every function here must never violate: a lookup for date D
may only ever return information from D or earlier. There is no
`get_price_on_or_after` in this module on purpose - anything computing a
historical statistic "as of" some date must not be able to peek forward.
"""

import bisect
import csv
from functools import lru_cache
from pathlib import Path

PRICES_DIR = Path(__file__).resolve().parent / "data" / "prices"


@lru_cache(maxsize=None)
def _load_bars(ticker: str):
    path = PRICES_DIR / f"{ticker.replace('.', '_')}.csv"
    if not path.exists():
        return (), ()
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: r["date"])  # defensive; fetch already writes in order
    dates = tuple(r["date"] for r in rows)
    closes = tuple(float(r["close"]) for r in rows)
    return dates, closes


def has_price_data(ticker: str) -> bool:
    dates, _ = _load_bars(ticker)
    return len(dates) > 0


def get_price_on_or_before(ticker: str, date_str: str):
    """Latest available close price at or before `date_str` (ISO
    YYYY-MM-DD). Returns None if there's no data for this ticker at all,
    or none as early as `date_str` (e.g. the ticker's data only starts
    later, or `date_str` predates this analysis's data floor)."""
    dates, closes = _load_bars(ticker)
    if not dates:
        return None
    idx = bisect.bisect_right(dates, date_str) - 1
    if idx < 0:
        return None
    return closes[idx]


def earliest_available_date(ticker: str):
    dates, _ = _load_bars(ticker)
    return dates[0] if dates else None


def clear_cache():
    _load_bars.cache_clear()
