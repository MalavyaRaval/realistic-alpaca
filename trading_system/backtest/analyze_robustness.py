"""Robustness analysis: ranks configs by how consistently well they do
across historical sub-periods (via average Calmar-ratio rank), not by
whichever single config happened to post the highest historical return.

The ranking itself uses only the 5 FULL calendar years (2021-2025), not the
two partial-year stubs (2020 H2, 2026 YTD). Annualizing a ~5-month or
partial-year return amplifies small timing noise into large Calmar swings,
and averaging 7 periods unweighted would let those two noisy stubs carry
the same influence as five full years despite representing much less
actual trading time. The partial periods are still computed and shown
(heatmap, full appendix) for reference - just not folded into the score
that decides "robust."
"""

import json
import statistics
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
DISPLAY_PERIODS = ["2020 H2", "2021", "2022", "2023", "2024", "2025", "2026 YTD"]
RANKING_PERIODS = ["2021", "2022", "2023", "2024", "2025"]  # full years only
FULL_PERIOD = "Full (2020 H2 - 2026 YTD)"


def calmar_sort_proxy(row):
    """Sortable stand-in for calmar_ratio. calmar_ratio is None exactly
    when there was zero drawdown to divide by - fall back to ranking those
    (rare/theoretical - never observed in this dataset) by annualized
    return instead, scaled well below any real Calmar value so a config
    with an actual, computed Calmar ratio is never outranked by one that
    merely had a lucky zero-drawdown stretch."""
    calmar = row["calmar_ratio"]
    if calmar is not None and calmar != "inf":
        return calmar
    return row["annualized_return"] / 1000 - 1e6


def main():
    with (DATA_DIR / "grid_results.json").open(encoding="utf-8") as f:
        results = json.load(f)

    by_period = {}
    for r in results:
        by_period.setdefault(r["period_name"], []).append(r)

    # Rank configs within each DISPLAY period by Calmar ratio (best = rank
    # 1) - used for the heatmap, covering all 7 periods for context.
    ranks_by_config = {}   # config_label -> {period_name: rank}
    for period_name in DISPLAY_PERIODS:
        rows = by_period.get(period_name, [])
        ranked = sorted(rows, key=calmar_sort_proxy, reverse=True)
        for rank, row in enumerate(ranked, start=1):
            ranks_by_config.setdefault(row["config_label"], {})[period_name] = rank

    full_by_config = {r["config_label"]: r for r in by_period.get(FULL_PERIOD, [])}

    robustness_rows = []
    for config_label, period_ranks in ranks_by_config.items():
        ranking_ranks = [period_ranks[p] for p in RANKING_PERIODS if p in period_ranks]
        avg_rank = statistics.mean(ranking_ranks)
        worst_rank = max(ranking_ranks)
        ranking_calmars = [
            calmar_sort_proxy(row)
            for p in RANKING_PERIODS
            for row in by_period[p] if row["config_label"] == config_label
        ]
        calmar_stdev = statistics.pstdev(ranking_calmars) if len(ranking_calmars) > 1 else 0.0
        negative_periods = sum(
            1 for p in DISPLAY_PERIODS
            for row in by_period[p]
            if row["config_label"] == config_label and row["total_return"] < 0
        )

        full = full_by_config.get(config_label, {})
        robustness_rows.append({
            "config_label": config_label,
            "initial_stop_pct": full.get("initial_stop_pct"),
            "trailing_activation_pct": full.get("trailing_activation_pct"),
            "trailing_distance_pct": full.get("trailing_distance_pct"),
            "avg_calmar_rank": avg_rank,
            "worst_calmar_rank": worst_rank,
            "calmar_stdev": calmar_stdev,
            "negative_periods_count": negative_periods,
            "full_period_total_return": full.get("total_return"),
            "full_period_annualized_return": full.get("annualized_return"),
            "full_period_max_drawdown": full.get("max_drawdown"),
            "full_period_calmar": full.get("calmar_ratio"),
            "full_period_trades": full.get("number_of_trades"),
            "full_period_win_rate": full.get("win_rate"),
        })

    robustness_rows.sort(key=lambda r: (r["avg_calmar_rank"], r["worst_calmar_rank"]))

    by_return_rows = sorted(
        [r for r in by_period.get(FULL_PERIOD, [])],
        key=lambda r: r["total_return"], reverse=True,
    )

    out = {
        "robustness_leaderboard": robustness_rows,
        "ranking_periods": RANKING_PERIODS,
        "display_periods": DISPLAY_PERIODS,
        "full_period_by_return": [
            {"config_label": r["config_label"], "total_return": r["total_return"],
             "max_drawdown": r["max_drawdown"], "avg_rank_across_periods":
                 next((rr["avg_calmar_rank"] for rr in robustness_rows if rr["config_label"] == r["config_label"]), None)}
            for r in by_return_rows
        ],
        "all_results": results,
    }

    out_path = DATA_DIR / "robustness_analysis.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print("Top 5 by robustness (avg Calmar rank across 5 full-year periods, 2021-2025):")
    for r in robustness_rows[:5]:
        print(f"  {r['config_label']}: avg_rank={r['avg_calmar_rank']:.1f}, worst_rank={r['worst_calmar_rank']}, "
              f"negative_periods={r['negative_periods_count']}/7, full_return={r['full_period_total_return']:.1%}")

    print()
    print("Top 5 by full-period total return alone (for contrast):")
    for r in by_return_rows[:5]:
        avg_rank = next((rr["avg_calmar_rank"] for rr in robustness_rows if rr["config_label"] == r["config_label"]), None)
        print(f"  {r['config_label']}: total_return={r['total_return']:.1%}, avg_robustness_rank={avg_rank:.1f}")

    print(f"\nSaved full analysis to {out_path}")


if __name__ == "__main__":
    main()
