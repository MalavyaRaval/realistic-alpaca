"""Single invocation of the ABEV trailing-stop monitor. Meant to be fired by
an external scheduler every 5 minutes - see the Windows Scheduled Task
"ABEV_TrailingStop_Monitor". Safe to call at any time; does nothing outside
market hours or while halted.
"""

from monitor import run_monitoring_cycle
from risk_manager import RiskLimits, RiskManager
from trailing_stop_strategy import TrailingStopParams, TrailingStopStrategy

SYMBOL = "ABEV"
QTY = 10

RISK_LIMITS = RiskLimits(
    max_position_size=QTY,
    max_dollar_exposure=35,
    max_portfolio_exposure=35,
    max_order_size=35,
    max_simultaneous_positions=1,
    max_daily_loss=10,
)


def main():
    strategy = TrailingStopStrategy(TrailingStopParams(symbol=SYMBOL, qty=QTY))
    risk_mgr = RiskManager(RISK_LIMITS)
    result = run_monitoring_cycle(strategy, risk_mgr)
    print(result)


if __name__ == "__main__":
    main()
