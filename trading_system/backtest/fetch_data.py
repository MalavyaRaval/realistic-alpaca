"""Fetches and caches historical daily bars for backtesting. Read-only
market data - never touches orders/account, never sends anything to
Alpaca's trading endpoint."""

import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from config import API_KEY, API_SECRET

DATA_DIR = Path(__file__).resolve().parent / "data"


def fetch_daily_bars(symbol: str, start: datetime, end: datetime) -> list:
    client = StockHistoricalDataClient(API_KEY, API_SECRET)
    bar_set = client.get_stock_bars(
        StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            feed=DataFeed.IEX,
            # TSLA split 5:1 (Aug 2020) and 3:1 (Aug 2022) - without this,
            # raw prices show a fake ~93% "crash" at each split date.
            adjustment=Adjustment.ALL,
        )
    )
    bars = bar_set[symbol]
    return [
        {
            "date": b.timestamp.date().isoformat(),
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
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
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [
            {
                "date": row["date"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
            }
            for row in reader
        ]


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "TSLA"
    start = datetime(2018, 1, 1, tzinfo=timezone.utc)
    end = datetime.now(timezone.utc)
    print(f"Fetching {symbol} daily bars from {start.date()} to {end.date()}...")
    bars = fetch_daily_bars(symbol, start, end)
    print(f"Got {len(bars)} bars. First: {bars[0]}. Last: {bars[-1]}")
    DATA_DIR.mkdir(exist_ok=True)
    out_path = DATA_DIR / f"{symbol}_daily.csv"
    save_csv(bars, out_path)
    print(f"Saved to {out_path}")
