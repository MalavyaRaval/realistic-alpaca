"""
Screens the live, tradable NASDAQ/NYSE common-stock universe for names whose
current share price fits inside a target budget, using real Alpaca market
data. This is a screener, not a forecast: it answers "what's affordable and
liquid right now", not "what will go up". Nothing here predicts future
price movement.
"""

import re
import sys
import time

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest
from alpaca.trading.enums import AssetClass, AssetExchange, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from config import API_KEY, API_SECRET, get_trading_client

BUDGET = float(sys.argv[1]) if len(sys.argv) > 1 else 100.0
# Alpaca's order cost-basis check applies a buffer above the raw quote (we
# saw ~6-10% on a prior AAPL test), so filter comfortably under the budget.
PRICE_CUTOFF = BUDGET / 1.10
MIN_VOLUME = 1_000_000  # keep the list to reasonably liquid, well-known names
SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")  # drop warrants/units/preferreds/etc.
BATCH_SIZE = 200


def fetch_candidate_symbols(trading_client):
    symbols = []
    for exchange in (AssetExchange.NASDAQ, AssetExchange.NYSE):
        assets = trading_client.get_all_assets(
            GetAssetsRequest(
                status=AssetStatus.ACTIVE,
                asset_class=AssetClass.US_EQUITY,
                exchange=exchange,
            )
        )
        for a in assets:
            if a.tradable and SYMBOL_RE.match(a.symbol):
                symbols.append(a.symbol)
    return sorted(set(symbols))


def fetch_snapshots(data_client, symbols):
    snapshots = {}
    for i in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[i : i + BATCH_SIZE]
        result = data_client.get_stock_snapshot(
            StockSnapshotRequest(symbol_or_symbols=batch)
        )
        snapshots.update(result)
        print(
            f"  fetched snapshots {min(i + BATCH_SIZE, len(symbols))}/{len(symbols)}",
            file=sys.stderr,
        )
        time.sleep(0.35)
    return snapshots


def main():
    trading_client = get_trading_client()
    data_client = StockHistoricalDataClient(API_KEY, API_SECRET)

    print("Fetching tradable NASDAQ/NYSE common-stock universe...", file=sys.stderr)
    symbols = fetch_candidate_symbols(trading_client)
    print(f"Candidate universe: {len(symbols)} symbols", file=sys.stderr)

    snapshots = fetch_snapshots(data_client, symbols)

    rows = []
    for symbol, snap in snapshots.items():
        if snap is None:
            continue
        price = None
        if snap.latest_trade and snap.latest_trade.price:
            price = snap.latest_trade.price
        elif snap.daily_bar and snap.daily_bar.close:
            price = snap.daily_bar.close

        volume = snap.daily_bar.volume if snap.daily_bar else None
        day_change_pct = None
        if snap.daily_bar and snap.previous_daily_bar and snap.previous_daily_bar.close:
            day_change_pct = (
                (snap.daily_bar.close - snap.previous_daily_bar.close)
                / snap.previous_daily_bar.close
                * 100
            )

        if price is None or volume is None:
            continue
        if not (1 <= price <= PRICE_CUTOFF):
            continue
        if volume < MIN_VOLUME:
            continue

        rows.append((symbol, price, day_change_pct, volume))

    rows.sort(key=lambda r: r[3], reverse=True)

    print(f"\n=== Affordable & liquid names under ~${PRICE_CUTOFF:.2f} (budget ${BUDGET:.0f}) ===")
    print(f"{'Symbol':<8}{'Price':>10}{'Day chg %':>12}{'Volume':>16}")
    for symbol, price, chg, vol in rows[:40]:
        chg_str = f"{chg:+.2f}" if chg is not None else "n/a"
        print(f"{symbol:<8}{price:>10.2f}{chg_str:>12}{vol:>16,}")
    print(f"\n{len(rows)} names matched out of {len(snapshots)} scanned.")


if __name__ == "__main__":
    main()
