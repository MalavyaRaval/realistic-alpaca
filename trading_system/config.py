import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("ALPACA_SECRET_KEY")
BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")

PAPER_HOST = "paper-api.alpaca.markets"


def get_trading_client():
    from alpaca.trading.client import TradingClient

    if not API_KEY or not API_SECRET:
        raise RuntimeError(
            "Missing ALPACA_API_KEY / ALPACA_SECRET_KEY. Set them in the .env "
            "file at the project root before running this script."
        )
    if PAPER_HOST not in BASE_URL:
        raise RuntimeError(
            f"Refusing to connect: ALPACA_BASE_URL ({BASE_URL}) is not the paper "
            f"trading endpoint. This trading system is configured for paper "
            f"trading only and will not connect to a live endpoint."
        )
    # alpaca-py appends "/v2" to whatever base URL it's given, so strip a
    # trailing "/v2" here to avoid ending up with ".../v2/v2/account".
    root_url = BASE_URL[: -len("/v2")] if BASE_URL.endswith("/v2") else BASE_URL
    return TradingClient(API_KEY, API_SECRET, paper=True, url_override=root_url)
