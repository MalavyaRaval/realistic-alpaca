"""Checks against the connected brokerage itself - not the local
database - for whether a candidate can actually be traded at all.
"""

from alpaca.common.exceptions import APIError
from alpaca.trading.enums import AssetClass


def check_tradable(client, ticker: str) -> tuple:
    """Returns (tradable: bool, reason: str). Fails closed: any lookup
    error (unknown symbol, API failure) is treated as NOT tradable rather
    than assumed fine.

    Checks the BROKER's own asset_class, not just its tradable flag - the
    disclosure's self-reported asset_type (checked separately via
    is_confirmed_equity below) says what the politician's filing claimed
    was traded; this confirms what the connected brokerage actually
    thinks the ticker itself is. A ticker that resolves to a tradable
    non-equity instrument (e.g. crypto) at the broker must not be bought
    as if it were the disclosed common stock just because it happens to
    share a symbol."""
    try:
        asset = client.get_asset(ticker)
    except APIError as e:
        return False, f"broker does not recognize this symbol: {e}"
    except Exception as e:
        return False, f"could not verify tradability with the broker: {e}"

    if not asset.tradable:
        return False, f"broker marks {ticker} as not tradable (status={asset.status})"
    if asset.asset_class != AssetClass.US_EQUITY:
        return False, f"broker classifies {ticker} as {asset.asset_class}, not US equity"
    return True, f"tradable (asset_class={asset.asset_class}, status={asset.status})"


# An ALLOWLIST, not a denylist, and deliberately so. The congress dataset
# carries both spelled-out asset types ("Stock", "Stock Option",
# "Municipal Security", "Cryptocurrency", "Corporate Bond", ...) and
# unexplained two-letter House Clerk abbreviation codes ("ST", "OP",
# "CT", "GS", "AB", ...) whose complete official mapping isn't something
# this module is confident it has exactly right. A disclosed options
# position frequently carries the UNDERLYING stock's ticker (e.g. "AAPL
# call options" disclosed with ticker "AAPL") - trading that ticker as a
# plain stock purchase would silently substitute a completely different
# instrument and risk profile for what was actually disclosed. Given
# that risk, only asset types this module can confidently confirm mean
# "ordinary common stock" are allowed through; everything else - a
# recognized option code, an unrecognized abbreviation, a bond, a fund,
# crypto, anything - is rejected by default rather than guessed at.
CONFIRMED_EQUITY_ASSET_TYPES = {"stock", "st"}

OPTIONS_ASSET_TYPES = {"stock option", "op", "options", "option"}


def is_confirmed_equity(asset_type: str) -> bool:
    if not asset_type:
        return False
    return asset_type.strip().lower() in CONFIRMED_EQUITY_ASSET_TYPES


def is_options_disclosure(asset_type: str) -> bool:
    """Used only to give a more specific rejection reason when an asset
    type is confidently identifiable as an option - NOT the gate itself.
    The actual gate is is_confirmed_equity()'s allowlist, since plenty of
    asset types this doesn't recognize as options still aren't safe
    common-stock purchases either (bonds, funds, crypto, unexplained
    codes)."""
    if not asset_type:
        return False
    return asset_type.strip().lower() in OPTIONS_ASSET_TYPES
