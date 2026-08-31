"""Single invocation of the congress-copy strategy. Meant to be fired
by an external scheduler every 5 minutes - see the GitHub Actions
workflow "Congress Mirror Monitor". Safe to call at any time: refreshes
the lightweight live database, then runs one cycle behind the same
market-hours/halt gate as ABEV's monitor.
"""

import database
from congress_mirror_strategy import CongressCopyParams, CongressCopyStrategy
from congress_monitor import run_congress_mirror_cycle
from live_refresh import refresh_live_db, LIVE_DB_PATH
from risk_manager import RiskLimits, RiskManager

# CongressCopyStrategy computes its own dynamic, percentage-of-equity risk
# limits fresh for every candidate (see congress_mirror_strategy.py's
# _entry_risk_manager) - the only field from this object that actually
# reaches the strategy's entry path is max_daily_loss, the account-wide
# circuit breaker carried over from the caller. The other fields are
# unused placeholders (kept non-None only because RiskLimits requires
# them): the strategy builds its OWN dedicated no-entry RiskManager
# internally for managing already-tracked positions (see
# congress_mirror_strategy.py's _no_entry_risk_manager), specifically so
# that guarantee does not depend on this caller ever passing zeroed-out
# fields here - these could safely be given real values without changing
# what the strategy allows.
OUTER_RISK_LIMITS = RiskLimits(
    max_position_size=0,
    max_dollar_exposure=0,
    max_portfolio_exposure=0,
    max_order_size=0,
    max_simultaneous_positions=0,
    max_daily_loss=10,
)


# True (the safe default): a candidate that passes every gate is
# reported, never submitted - see CongressCopyStrategy's dry_run
# docstring. Only flip this to False after a human has actually seen and
# confirmed a specific real proposed trade from this exact pipeline.
DRY_RUN = True


def main():
    database.DB_PATH = LIVE_DB_PATH
    n = refresh_live_db()
    print(f"Live database refreshed: {n} rows")

    strategy = CongressCopyStrategy(CongressCopyParams(), dry_run=DRY_RUN)
    risk_mgr = RiskManager(OUTER_RISK_LIMITS)
    result = run_congress_mirror_cycle(strategy, risk_mgr)
    print(result)


if __name__ == "__main__":
    main()
