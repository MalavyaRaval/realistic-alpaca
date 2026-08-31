"""Tests for broker_checks.py - the connected-brokerage tradability gate
and the confirmed-equity/options asset-type allowlists. The fake client
below stands in for alpaca-py's TradingClient; nothing here makes a real
network call.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpaca.common.exceptions import APIError
from broker_checks import check_tradable, is_confirmed_equity, is_options_disclosure


class FakeClient:
    def __init__(self, asset=None, raises=None):
        self._asset = asset
        self._raises = raises

    def get_asset(self, ticker):
        if self._raises is not None:
            raise self._raises
        return self._asset


def test_check_tradable_true_for_tradable_asset():
    client = FakeClient(asset=SimpleNamespace(tradable=True, status="active", asset_class="us_equity"))
    tradable, reason = check_tradable(client, "AAPL")
    assert tradable is True
    assert "tradable" in reason


def test_check_tradable_false_when_broker_marks_not_tradable():
    client = FakeClient(asset=SimpleNamespace(tradable=False, status="inactive", asset_class="us_equity"))
    tradable, reason = check_tradable(client, "DELISTED")
    assert tradable is False
    assert "not tradable" in reason


def test_check_tradable_fails_closed_on_api_error():
    client = FakeClient(raises=APIError("asset not found"))
    tradable, reason = check_tradable(client, "NOTAREALTICKER")
    assert tradable is False
    assert "not recognize" in reason


def test_check_tradable_fails_closed_on_unexpected_exception():
    client = FakeClient(raises=RuntimeError("network down"))
    tradable, reason = check_tradable(client, "AAPL")
    assert tradable is False
    assert "could not verify" in reason


def test_check_tradable_false_for_non_equity_asset_class():
    client = FakeClient(asset=SimpleNamespace(tradable=True, status="active", asset_class="crypto"))
    tradable, reason = check_tradable(client, "BTCUSD")
    assert tradable is False
    assert "not US equity" in reason


def test_is_confirmed_equity_accepts_spelled_out_and_abbreviated_codes():
    assert is_confirmed_equity("Stock") is True
    assert is_confirmed_equity("stock") is True
    assert is_confirmed_equity("  Stock  ") is True
    assert is_confirmed_equity("ST") is True
    assert is_confirmed_equity("st") is True


def test_is_confirmed_equity_rejects_anything_not_on_the_allowlist():
    assert is_confirmed_equity("Stock Option") is False
    assert is_confirmed_equity("Corporate Bond") is False
    assert is_confirmed_equity("Cryptocurrency") is False
    assert is_confirmed_equity("GS") is False  # an unexplained House Clerk abbreviation
    assert is_confirmed_equity("") is False
    assert is_confirmed_equity(None) is False


def test_is_options_disclosure_recognizes_option_codes():
    assert is_options_disclosure("Stock Option") is True
    assert is_options_disclosure("OP") is True
    assert is_options_disclosure("option") is True
    assert is_options_disclosure("Options") is True


def test_is_options_disclosure_false_for_non_option_types():
    assert is_options_disclosure("Stock") is False
    assert is_options_disclosure("Corporate Bond") is False
    assert is_options_disclosure(None) is False
