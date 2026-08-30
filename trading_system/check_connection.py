from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from config import BASE_URL, PAPER_HOST, get_trading_client


def main():
    client = get_trading_client()
    account = client.get_account()

    is_paper = PAPER_HOST in BASE_URL

    print("=== Alpaca Connection Report ===")
    print(f"API connection: OK")
    print(f"Environment: {'PAPER' if is_paper else 'LIVE'} ({BASE_URL})")
    print(f"Account number: {account.account_number}")
    print(f"Account status: {account.status}")
    print(f"Trading blocked: {account.trading_blocked}")
    print(f"Account blocked: {account.account_blocked}")
    print(f"Pattern day trader: {account.pattern_day_trader}")
    print()
    print(f"Equity: ${account.equity}")
    print(f"Cash: ${account.cash}")
    print(f"Buying power: ${account.buying_power}")

    positions = client.get_all_positions()
    print(f"\nOpen positions: {len(positions)}")
    for p in positions:
        print(
            f"  {p.symbol}: qty={p.qty}, market_value=${p.market_value}, "
            f"unrealized_pl=${p.unrealized_pl}"
        )

    orders = client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
    print(f"\nOpen orders: {len(orders)}")
    for o in orders:
        print(
            f"  {o.symbol}: side={o.side}, qty={o.qty}, type={o.order_type}, "
            f"status={o.status}"
        )


if __name__ == "__main__":
    main()
