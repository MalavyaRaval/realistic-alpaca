"""Live tests against Alpaca's paper/data endpoints - read-only, no orders."""

import pytest

import market_data

SYMBOL = "AAPL"


def test_is_market_open_returns_a_bool():
    result = market_data.is_market_open()
    assert isinstance(result, bool)


def test_get_current_price_returns_positive_float():
    price = market_data.get_current_price(SYMBOL)
    assert isinstance(price, float)
    assert price > 0


def test_get_volume_returns_positive_int():
    volume = market_data.get_volume(SYMBOL)
    assert isinstance(volume, int)
    assert volume > 0


def test_get_historical_bars_returns_ordered_bars():
    bars = market_data.get_historical_bars(SYMBOL, days=5)
    assert len(bars) > 0
    for bar in bars:
        assert bar["high"] >= bar["low"]
        assert bar["close"] > 0
        assert bar["volume"] >= 0
    timestamps = [b["timestamp"] for b in bars]
    assert timestamps == sorted(timestamps)


def test_unknown_symbol_fails_closed_not_silently():
    with pytest.raises(market_data.MarketDataUnavailable):
        market_data.get_current_price("ZZZZZNOTAREALTICKER")
