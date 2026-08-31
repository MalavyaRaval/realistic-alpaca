"""READ-ONLY preview of what the congress-copy strategy would do on its
very next cycle, against REAL current account state and REAL current
disclosure data. Submits no orders. Saves no state.

This exists so the actual first proposed trade can be shown and
confirmed before congress_mirror_run_once.py is ever run live or the
GitHub Actions workflow is re-enabled - per the requirement that the
first copied trade be shown and confirmed before it happens.

Run: python preview_first_trade.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import account
import database
import mirror_state
from congress_mirror_strategy import CongressCopyParams, CongressCopyStrategy
from live_refresh import LIVE_DB_PATH, refresh_live_db


def main():
    database.DB_PATH = LIVE_DB_PATH
    n = refresh_live_db()
    print(f"Live database refreshed: {n} rows\n")

    acct = account.get_account_summary()
    positions = account.get_positions()
    open_orders = account.get_open_orders()
    state = mirror_state.load_state()  # read-only here: never saved back

    print("=== Account (real, current) ===")
    print(f"equity=${acct.equity:.2f}  cash=${acct.cash:.2f}  buying_power=${acct.buying_power:.2f}")
    print(f"open positions: {[p.symbol for p in positions] or 'none'}")
    print(f"strategy state: open_position_tickers={state['open_position_tickers']}  halted={state['halted']}")
    print()

    params = CongressCopyParams()
    strategy = CongressCopyStrategy(params)
    print("=== Configured limits ===")
    print(f"max_single_security_pct={params.max_single_security_pct:.0%} of equity "
          f"= ${acct.equity * params.max_single_security_pct:.2f}")
    print(f"max_strategy_pct={params.max_strategy_pct:.0%} of equity "
          f"= ${acct.equity * params.max_strategy_pct:.2f}")
    print(f"max_positions={params.max_positions}  max_disclosure_age_days={params.max_disclosure_age_days}")
    print(f"allowed_chambers={params.allowed_chambers}")
    print()

    pending_notional = strategy._pending_buy_notional(open_orders, state["open_position_tickers"])
    print(f"pending (unfilled) BUY order exposure for this strategy: "
          f"{'unknown - price lookup failed' if pending_notional is None else f'${pending_notional:.2f}'}")
    print()

    candidates = database.get_new_purchase_candidates(
        seen_ids=state["seen_transaction_ids"],
        chambers=params.allowed_chambers,
        limit=10,
    )
    print(f"=== {len(candidates)} new disclosed purchase(s) to consider (most recently disclosed first) ===\n")

    if not candidates:
        print("Nothing new to consider right now - re-run this closer to a fresh House PTR filing.")
        return

    first_approved = None
    for c in candidates:
        print(f"--- {c.ticker} ({c.security_name}) ---")
        print(f"  politician: {c.politician_name} ({c.party}-{c.state}, {c.office})")
        print(f"  transaction_date: {c.transaction_date}   disclosure_date: {c.disclosure_date}")
        print(f"  disclosure_age_days: {c.disclosure_age_days}")
        print(f"  amount_range: {c.amount_range_label}")
        print(f"  asset_type: {c.asset_type!r}")
        print(f"  source_document_url: {c.source_document_url}")

        decision = strategy.decide_candidate(c, acct, positions, state["open_position_tickers"], pending_notional)
        verdict = "APPROVED" if decision.approved else "rejected"
        print(f"  -> {verdict}: {decision.reason}")
        print()

        if decision.approved and first_approved is None:
            first_approved = (c, decision)

    print("=" * 70)
    if first_approved is None:
        print("No candidate in this batch would currently result in a trade.")
        cheapest_note = (
            f"Note the practical tension on a $100 account: the 5% single-security "
            f"cap is ${acct.equity * params.max_single_security_pct:.2f}. Any candidate "
            f"priced above that per share cannot be bought at all under this limit, "
            f"regardless of how it scores on every other gate. This is expected "
            f"behavior of the configured risk limits, not a bug - it means a very "
            f"small account may go for long stretches (or indefinitely, at current "
            f"prices) without being able to open a position at all."
        )
        print(cheapest_note)
    else:
        c, d = first_approved
        print("PROPOSED FIRST TRADE (nothing has been submitted):")
        print(f"  BUY {d.qty} share(s) of {c.ticker} (~${d.dollar_amount:.2f})")
        print(f"  copying {c.politician_name}'s disclosed purchase, transaction_date={c.transaction_date}, "
              f"disclosed {c.disclosure_age_days} days later on {c.disclosure_date}")
        print(f"  sizing reason: {d.reason}")
        print(f"  then managed by the trailing-stop exit logic: "
              f"{params.initial_stop_pct:.0%} initial stop, {params.trailing_activation_pct:.0%} "
              f"activation, {params.trailing_distance_pct:.0%} trailing distance")
        print()
        print("This trade has NOT been submitted. Nothing is live yet.")


if __name__ == "__main__":
    main()
