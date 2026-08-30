"""Correctness tests for the backtest engine - synthetic bars with known,
hand-computable outcomes. These matter more than usual: a subtle lookahead
bug here would produce confidently wrong "robust" conclusions."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from backtest_engine import BacktestConfig, run_backtest
from metrics import compute_metrics


def bar(date, open_, high, low, close, volume=1_000_000):
    return {"date": date, "open": open_, "high": high, "low": low, "close": close, "volume": volume}


def test_basic_stop_out_no_activation():
    bars = [
        bar("2020-01-01", 100, 101, 99, 100),
        bar("2020-01-02", 99, 100, 89, 90),  # low breaches the 10% initial stop
    ]
    config = BacktestConfig(initial_stop_pct=0.10, trailing_activation_pct=0.50, trailing_distance_pct=0.05)
    result = run_backtest(bars, config)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "stopped_out"
    assert trade.exit_date == "2020-01-02"
    # open (99) was above the stop (~90.03) - not a gap, fill should be near the stop level, not the open
    assert 89 < trade.exit_price < 91


def test_gap_down_through_stop_fills_at_open_not_stop_level():
    bars = [
        bar("2020-01-01", 100, 101, 99, 100),
        bar("2020-01-02", 80, 85, 78, 82),  # opens well below the ~90 stop - a gap
    ]
    config = BacktestConfig(initial_stop_pct=0.10, trailing_activation_pct=0.50, trailing_distance_pct=0.05)
    result = run_backtest(bars, config)

    trade = result.trades[0]
    # fill must be based on the open (~80), not the theoretical stop (~90) -
    # a real stop order can't fill better than the first available price.
    assert trade.exit_price < 82
    assert trade.exit_price > 78


def test_no_lookahead_ratchet_takes_effect_next_bar_not_same_bar():
    bars = [
        bar("2020-01-01", 100, 101, 99, 100),   # entry
        bar("2020-01-02", 100, 200, 95, 150),   # spikes, activates trailing, ratchets stop way up
        bar("2020-01-03", 180, 185, 170, 175),  # low (170) is BELOW day1's stop (~80) but should
                                                 # trigger against day2's ratcheted stop (~190), not day1's
    ]
    config = BacktestConfig(initial_stop_pct=0.20, trailing_activation_pct=0.05, trailing_distance_pct=0.05)
    result = run_backtest(bars, config)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_date == "2020-01-03"
    # if day2's high had (wrongly) been allowed to affect day2's own exit
    # check, day2 would never have been evaluated against the pre-ratchet
    # stop; the real assertion is that day3 exits (170 < ratcheted ~190),
    # which would NOT happen if the ratchet were somehow skipped and the
    # stop had stayed at day1's ~80 (170 > 80, no trigger).
    assert trade.exit_price < 185


def test_stop_never_decreases_during_a_pullback():
    bars = [
        bar("2020-01-01", 100, 101, 99, 100),   # entry
        bar("2020-01-02", 100, 150, 99, 145),   # activates (>=105), ratchets stop to 150*0.95=142.5
        bar("2020-01-03", 148, 149, 143, 145),  # pulls back, high (149) is LOWER than day2's high (150);
                                                 # low (143) stays above the ratcheted stop (142.5) - no exit
        bar("2020-01-04", 160, 200, 155, 195),  # new high, stop should ratchet again from 150, not 142
    ]
    config = BacktestConfig(initial_stop_pct=0.30, trailing_activation_pct=0.05, trailing_distance_pct=0.05)
    result = run_backtest(bars, config)

    stops_by_date = dict(result.stop_history)
    assert stops_by_date["2020-01-02"] == pytest.approx(150 * 0.95)
    # day3's pullback (lower high than day2) must NOT lower the stop
    assert stops_by_date["2020-01-03"] == stops_by_date["2020-01-02"]
    assert stops_by_date["2020-01-03"] <= stops_by_date["2020-01-04"]
    # never any decrease anywhere in the recorded history
    values = [v for _, v in result.stop_history]
    assert all(b >= a - 1e-9 for a, b in zip(values, values[1:]))


def test_reenters_after_being_stopped_out():
    bars = [
        bar("2020-01-01", 100, 101, 99, 100),
        bar("2020-01-02", 99, 100, 89, 90),   # stopped out
        bar("2020-01-03", 91, 92, 90, 91),    # fresh entry
        bar("2020-01-04", 91, 92, 90, 91),    # holding, no exit
    ]
    config = BacktestConfig(initial_stop_pct=0.10, trailing_activation_pct=0.50, trailing_distance_pct=0.05)
    result = run_backtest(bars, config)

    assert len(result.trades) == 2
    assert result.trades[0].exit_reason == "stopped_out"
    assert result.trades[1].entry_date == "2020-01-03"
    assert result.trades[1].exit_reason == "period_end"


def test_no_same_day_entry_and_exit():
    # A huge drop on the entry day itself must not be checked as an exit -
    # the stop is only just being established at that day's open.
    bars = [
        bar("2020-01-01", 100, 101, 50, 55),  # would breach any reasonable stop intraday
    ]
    config = BacktestConfig(initial_stop_pct=0.10, trailing_activation_pct=0.50, trailing_distance_pct=0.05)
    result = run_backtest(bars, config)
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "period_end"


def test_metrics_on_known_trades():
    bars = [
        bar("2020-01-01", 100, 101, 99, 100),
        bar("2020-01-02", 99, 100, 89, 90),    # loss
        bar("2020-01-03", 91, 92, 89, 90),     # re-entry, then stopped out again (loss)
        bar("2020-01-04", 89, 90, 79, 80),
        bar("2020-01-05", 81, 82, 80, 81),     # re-entry, held to period end (likely a gain given drift up)
        bar("2020-01-06", 82, 120, 81, 118),
    ]
    config = BacktestConfig(initial_stop_pct=0.10, trailing_activation_pct=0.50, trailing_distance_pct=0.05)
    result = run_backtest(bars, config)
    m = compute_metrics(result)

    assert m["number_of_trades"] == len(result.trades)
    assert 0.0 <= m["win_rate"] <= 1.0
    assert m["exposure"] == 1.0  # in a position every single day in this sequence
    assert m["largest_loss_pct"] <= 0
    assert m["turnover"] > 0
