"""Single invocation of the congress-mirror strategy. Meant to be fired
by an external scheduler every 5 minutes - see the GitHub Actions
workflow "Congress Mirror Monitor". Safe to call at any time: refreshes
the lightweight live database, then runs one cycle behind the same
market-hours/halt gate as ABEV's monitor.
"""

import database
from congress_mirror_strategy import CongressMirrorParams, CongressMirrorStrategy
from congress_monitor import run_congress_mirror_cycle
from live_refresh import refresh_live_db, LIVE_DB_PATH
from risk_manager import RiskLimits, RiskManager

RISK_LIMITS = RiskLimits(
    max_position_size=1,
    max_dollar_exposure=55,
    max_portfolio_exposure=55,
    max_order_size=55,
    max_simultaneous_positions=1,
    max_daily_loss=10,
)


def main():
    database.DB_PATH = LIVE_DB_PATH
    n = refresh_live_db()
    print(f"Live database refreshed: {n} rows")

    strategy = CongressMirrorStrategy(CongressMirrorParams())
    risk_mgr = RiskManager(RISK_LIMITS)
    result = run_congress_mirror_cycle(strategy, risk_mgr)
    print(result)


if __name__ == "__main__":
    main()
