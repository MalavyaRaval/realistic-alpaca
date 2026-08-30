from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

from config import API_KEY, API_SECRET, BASE_URL, PAPER_HOST

SYMBOL = "AAPL"
QTY = 1


def main():
    is_paper = PAPER_HOST in BASE_URL
    if not is_paper:
        raise RuntimeError(
            f"Refusing: ALPACA_BASE_URL ({BASE_URL}) is not the paper trading "
            f"endpoint. Aborting preview."
        )

    data_client = StockHistoricalDataClient(API_KEY, API_SECRET)
    quote = data_client.get_stock_latest_quote(
        StockLatestQuoteRequest(symbol_or_symbols=SYMBOL)
    )[SYMBOL]

    bid = quote.bid_price
    ask = quote.ask_price
    if bid and ask:
        mid = (bid + ask) / 2
    else:
        mid = ask or bid

    print("=== Order Preview (NOT SUBMITTED) ===")
    print(f"Environment: PAPER ({BASE_URL})")
    print(f"Symbol: {SYMBOL}")
    print(f"Quantity: {QTY}")
    print(f"Side: BUY")
    print(f"Order type: MARKET, time_in_force=DAY")
    print(f"Latest bid: ${bid}")
    print(f"Latest ask: ${ask}")
    print(f"Estimated fill price (mid): ${mid:.2f}")
    print(f"Estimated order value: ${mid * QTY:.2f}")
    print(f"Quote timestamp (UTC): {quote.timestamp}")


if __name__ == "__main__":
    main()
