"""Market-data module: current price, historical prices, volume, and market
open/closed status.

Every function fails closed: if Alpaca returns an error, a missing symbol, or
a response with a field we need but doesn't have a usable value, we raise
MarketDataUnavailable rather than returning a fabricated or stale-looking
number. Callers (the engine) must treat that exception as "cannot trade right
now", not as "assume some default and continue".
"""

from datetime import datetime, timedelta, timezone

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
from alpaca.data.timeframe import TimeFrame

from config import API_KEY, API_SECRET, get_trading_client
from logging_setup import log_event

# Paper accounts only carry an IEX data subscription, not full SIP - pin the
# feed explicitly rather than relying on the client's default (SIP), which
# fails with "subscription does not permit querying recent SIP data".
DATA_FEED = DataFeed.IEX


class MarketDataUnavailable(Exception):
    """Raised whenever market data can't be retrieved or is incomplete."""


_data_client = None
_trading_client = None


def _data():
    global _data_client
    if _data_client is None:
        # Not routed through config.get_trading_client()'s PAPER_HOST gate
        # on purpose: Alpaca's market-data API is a single host shared by
        # paper and live accounts (there is no separate "live data" host
        # ALPACA_BASE_URL could point this at), and this client only ever
        # reads quotes/bars - it never places an order or touches account
        # state. The gate matters for TradingClient (order/account access);
        # it has nothing to check here.
        _data_client = StockHistoricalDataClient(API_KEY, API_SECRET)
    return _data_client


def _trading():
    global _trading_client
    if _trading_client is None:
        _trading_client = get_trading_client()
    return _trading_client


def is_market_open() -> bool:
    try:
        clock = _trading().get_clock()
    except Exception as e:
        log_event("error", source="market_data.is_market_open", message=str(e))
        raise MarketDataUnavailable(f"Could not retrieve market clock: {e}") from e

    log_event(
        "market_data",
        action="clock",
        is_open=clock.is_open,
        next_open=clock.next_open,
        next_close=clock.next_close,
    )
    return clock.is_open


def _get_snapshot(symbol: str):
    try:
        snapshots = _data().get_stock_snapshot(
            StockSnapshotRequest(symbol_or_symbols=[symbol], feed=DATA_FEED)
        )
    except Exception as e:
        log_event("error", source="market_data.snapshot", symbol=symbol, message=str(e))
        raise MarketDataUnavailable(f"Could not retrieve snapshot for {symbol}: {e}") from e

    snap = snapshots.get(symbol)
    if snap is None:
        log_event(
            "error", source="market_data.snapshot", symbol=symbol, message="no snapshot returned"
        )
        raise MarketDataUnavailable(f"No snapshot data returned for {symbol}")
    return snap


def get_current_price(symbol: str) -> float:
    snap = _get_snapshot(symbol)

    price = None
    if snap.latest_trade is not None and snap.latest_trade.price:
        price = snap.latest_trade.price
    elif snap.daily_bar is not None and snap.daily_bar.close:
        price = snap.daily_bar.close

    if price is None or price <= 0:
        log_event(
            "error", source="market_data.get_current_price", symbol=symbol,
            message="no usable price field on snapshot",
        )
        raise MarketDataUnavailable(f"No usable current price for {symbol}")

    price = float(price)
    log_event("market_data", action="current_price", symbol=symbol, price=price)
    return price


def get_volume(symbol: str) -> int:
    snap = _get_snapshot(symbol)

    if snap.daily_bar is None or snap.daily_bar.volume is None:
        log_event(
            "error", source="market_data.get_volume", symbol=symbol,
            message="no volume field on snapshot",
        )
        raise MarketDataUnavailable(f"No volume data for {symbol}")

    volume = int(snap.daily_bar.volume)
    log_event("market_data", action="volume", symbol=symbol, volume=volume)
    return volume


def get_historical_bars(symbol: str, days: int = 30) -> list:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days * 2 + 5)  # buffer for weekends/holidays

    try:
        bar_set = _data().get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                feed=DATA_FEED,
            )
        )
        symbol_bars = bar_set[symbol]
    except MarketDataUnavailable:
        raise
    except Exception as e:
        log_event(
            "error", source="market_data.get_historical_bars", symbol=symbol, message=str(e)
        )
        raise MarketDataUnavailable(f"Could not retrieve historical bars for {symbol}: {e}") from e

    if not symbol_bars:
        log_event(
            "error", source="market_data.get_historical_bars", symbol=symbol,
            message="no bars returned",
        )
        raise MarketDataUnavailable(f"No historical bars for {symbol}")

    bars = [
        {
            "timestamp": b.timestamp,
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": int(b.volume),
        }
        for b in symbol_bars[-days:]
    ]
    log_event(
        "market_data", action="historical_bars", symbol=symbol, bars_returned=len(bars)
    )
    return bars
