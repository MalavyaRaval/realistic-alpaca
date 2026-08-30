"""Fetches and caches daily price history for the analysis universe: the
300 most-traded tickers in the congress dataset's 2021+ window, SPY (the
market benchmark), and the SPDR sector benchmark ETFs.

Same source and split-adjustment approach as the backtest module
(Alpaca's free IEX feed, Adjustment.ALL) - that feed only carries history
back to ~2020-07-27, which becomes this analysis's real data-availability
floor, not a chosen cutoff.
"""

import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # trading_system/

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from config import API_KEY, API_SECRET

DATA_DIR = Path(__file__).resolve().parent / "data"
PRICES_DIR = DATA_DIR / "prices"

from sector_map import SECTOR_BENCHMARK_ETF

MARKET_BENCHMARK = "SPY"


def fetch_daily_bars(client, symbol: str, start: datetime, end: datetime) -> list:
    bar_set = client.get_stock_bars(
        StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            feed=DataFeed.IEX,
            adjustment=Adjustment.ALL,
        )
    )
    try:
        bars = bar_set[symbol]
    except KeyError:
        return []
    return [
        {
            "date": b.timestamp.date().isoformat(),
            "open": float(b.open), "high": float(b.high),
            "low": float(b.low), "close": float(b.close),
            "volume": int(b.volume),
        }
        for b in bars
    ]


def save_csv(bars: list, path: Path):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(bars)


def load_csv(path: Path) -> list:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [
            {**row, "open": float(row["open"]), "high": float(row["high"]),
             "low": float(row["low"]), "close": float(row["close"]),
             "volume": int(row["volume"])}
            for row in csv.DictReader(f)
        ]


def fetch_all(tickers_path: Path, force: bool = False):
    PRICES_DIR.mkdir(parents=True, exist_ok=True)
    client = StockHistoricalDataClient(API_KEY, API_SECRET)

    with tickers_path.open(encoding="utf-8") as f:
        tickers = json.load(f)

    universe = sorted(set(tickers) | {MARKET_BENCHMARK} | set(SECTOR_BENCHMARK_ETF.values()))
    print(f"Fetching {len(universe)} symbols (universe + benchmark + sector ETFs)")

    start = datetime(2018, 1, 1, tzinfo=timezone.utc)
    end = datetime.now(timezone.utc)

    failures = []
    for i, symbol in enumerate(universe, start=1):
        out_path = PRICES_DIR / f"{symbol.replace('.', '_')}.csv"
        if out_path.exists() and not force:
            continue
        try:
            bars = fetch_daily_bars(client, symbol, start, end)
        except Exception as e:
            failures.append((symbol, str(e)))
            continue
        if not bars:
            failures.append((symbol, "no bars returned"))
            continue
        save_csv(bars, out_path)
        if i % 25 == 0:
            print(f"  fetched {i}/{len(universe)}")
        time.sleep(0.05)

    print(f"Done. {len(universe) - len(failures)} succeeded, {len(failures)} failed.")
    if failures:
        (DATA_DIR / "price_fetch_failures.json").write_text(json.dumps(failures, indent=2))
        print(f"Failures logged to {DATA_DIR / 'price_fetch_failures.json'}")


if __name__ == "__main__":
    fetch_all(Path(__file__).resolve().parent.parent / "analysis_top_tickers.json")
