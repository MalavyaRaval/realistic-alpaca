"""Fail-closed behavior: if the API misbehaves (here, simulated with
deliberately invalid credentials against the real paper endpoint - never a
live endpoint), modules must raise rather than return a plausible-looking
but wrong value."""

import pytest
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.client import TradingClient

import account
import market_data
from config import BASE_URL, PAPER_HOST


def _bad_paper_trading_client():
    root_url = BASE_URL[: -len("/v2")] if BASE_URL.endswith("/v2") else BASE_URL
    assert PAPER_HOST in root_url, "refusing to build a fail-closed test client against a non-paper URL"
    return TradingClient("not-a-real-key", "not-a-real-secret", paper=True, url_override=root_url)


def test_market_data_fails_closed_on_bad_trading_credentials():
    original = market_data._trading_client
    market_data._trading_client = _bad_paper_trading_client()
    try:
        with pytest.raises(market_data.MarketDataUnavailable):
            market_data.is_market_open()
    finally:
        market_data._trading_client = original


def test_account_fails_closed_on_bad_trading_credentials():
    original = account._trading_client
    account._trading_client = _bad_paper_trading_client()
    try:
        with pytest.raises(account.AccountDataUnavailable):
            account.get_account_summary()
    finally:
        account._trading_client = original


def test_market_data_fails_closed_on_bad_data_credentials():
    original = market_data._data_client
    market_data._data_client = StockHistoricalDataClient("not-a-real-key", "not-a-real-secret")
    try:
        with pytest.raises(market_data.MarketDataUnavailable):
            market_data.get_current_price("AAPL")
    finally:
        market_data._data_client = original
