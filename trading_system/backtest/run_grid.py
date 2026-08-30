"""Runs the full parameter grid across multiple historical sub-periods and
saves results to JSON. Pure historical simulation - no Alpaca orders."""

import json
import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest_engine import BacktestConfig, run_backtest
from fetch_data import DATA_DIR, load_csv
from metrics import compute_metrics

INITIAL_STOP_OPTIONS = [0.05, 0.10, 0.15]
TRAILING_ACTIVATION_OPTIONS = [0.05, 0.10, 0.15]
TRAILING_DISTANCE_OPTIONS = [0.03, 0.05, 0.08, 0.10]

PERIODS = [
    ("2020 H2", "2020-07-27", "2020-12-31"),
    ("2021", "2021-01-01", "2021-12-31"),
    ("2022", "2022-01-01", "2022-12-31"),
    ("2023", "2023-01-01", "2023-12-31"),
    ("2024", "2024-01-01", "2024-12-31"),
    ("2025", "2025-01-01", "2025-12-31"),
    ("2026 YTD", "2026-01-01", "2026-12-31"),
    ("Full (2020 H2 - 2026 YTD)", "2020-07-27", "2026-12-31"),
]


def slice_bars(bars, start, end):
    return [b for b in bars if start <= b["date"] <= end]


def main():
    bars = load_csv(DATA_DIR / "TSLA_daily.csv")
    print(f"Loaded {len(bars)} bars covering {bars[0]['date']} to {bars[-1]['date']}")

    configs = [
        BacktestConfig(initial_stop_pct=s, trailing_activation_pct=a, trailing_distance_pct=t)
        for s, a, t in product(INITIAL_STOP_OPTIONS, TRAILING_ACTIVATION_OPTIONS, TRAILING_DISTANCE_OPTIONS)
    ]
    print(f"{len(configs)} configurations x {len(PERIODS)} periods = {len(configs) * len(PERIODS)} backtests")

    all_results = []
    for period_name, start, end in PERIODS:
        period_bars = slice_bars(bars, start, end)
        if not period_bars:
            print(f"WARNING: no bars for period {period_name} ({start} to {end}), skipping")
            continue
        for config in configs:
            result = run_backtest(period_bars, config)
            metrics = compute_metrics(result)
            metrics["period_name"] = period_name
            all_results.append(metrics)

    out_path = Path(__file__).resolve().parent / "data" / "grid_results.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print(f"Saved {len(all_results)} result rows to {out_path}")


if __name__ == "__main__":
    main()
