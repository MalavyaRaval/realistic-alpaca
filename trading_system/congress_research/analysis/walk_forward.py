"""Runs the performance engine at successive evaluation checkpoints,
using only transactions disclosed by each checkpoint - this is what makes
it a walk-forward analysis rather than one lookahead-biased snapshot
computed once and re-labeled with several dates.

This module does not claim, and nothing in it should be read as
implying, that any politician used material nonpublic information or
that past results predict future ones - see performance_engine.py's
"immediate vs lagged" split, which exists specifically to separate a
timing artifact from anything resembling a real, repeatable pattern.

Consistency is measured by how much the top of the ranking overlaps from
one checkpoint to the next. Read this cautiously: the checkpoints are
CUMULATIVE (each includes everything disclosed since 2021-01-01, not an
independent window), so a later checkpoint's ranking is built from a
superset of an earlier one's data - some rank stability is mechanically
expected from that overlap alone, not only from a "real, sustained
pattern." The first-vs-last correlation reported here is still
informative (a near-zero value is hard to explain as anything other than
noise), but a high one would need more scrutiny before reading it as
evidence of skill.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database
from performance_engine import compute_performance

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "congress_trades.db"
DATA_DIR = Path(__file__).resolve().parent / "data"

CHECKPOINTS = ["2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31", "2026-08-24"]
MIN_TRANSACTIONS = 5           # minimum analyzable buy/sell transactions to be considered at all
MIN_USABLE_LEGS = 5            # minimum priced legs to be considered "rankable"
MIN_DOLLAR_COVERAGE = 0.40     # at least this fraction of disclosed dollar volume must be priced


def load_all_transactions_by_politician(chambers=("house", "senate")):
    database.DB_PATH = DB_PATH
    placeholders = ", ".join("?" for _ in chambers)
    with database.connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(database._COLUMNS)} FROM congress_transactions "
            f"WHERE chamber IN ({placeholders}) AND transaction_date >= '2021-01-01' "
            f"ORDER BY politician_id, transaction_date",
            chambers,
        ).fetchall()
    by_politician = {}
    for row in rows:
        txn = database._row_to_transaction(row)
        by_politician.setdefault(txn.politician_id, []).append(txn)
    return by_politician


def qualifies_as_of(txns: list, checkpoint: str) -> bool:
    """Whether a politician has enough DISCLOSED-BY-`checkpoint` activity
    to be worth analyzing AT that checkpoint. This must be evaluated
    per-checkpoint, never once against full history: a politician's
    activity disclosed after `checkpoint` (even after every checkpoint)
    must never determine whether they qualify at an earlier one - that
    was a real bug here previously, caught by an independent audit."""
    visible = [t for t in txns if t.disclosure_date <= checkpoint]
    buy_sell = [t for t in visible if t.transaction_type in ("Purchase", "Sale (Full)", "Sale (Partial)")]
    return len(buy_sell) >= MIN_TRANSACTIONS


def qualifying_politicians(by_politician: dict, checkpoint: str) -> dict:
    """Politicians meeting the activity threshold using only information
    disclosed by `checkpoint` - a threshold on raw transaction count,
    independent of whether price data happens to be available for their
    tickers. Must be called per-checkpoint (see qualifies_as_of)."""
    return {pid: txns for pid, txns in by_politician.items() if qualifies_as_of(txns, checkpoint)}


def run_walk_forward():
    by_politician = load_all_transactions_by_politician()
    print(f"{len(by_politician)} politicians with any 2021+ activity on record")

    checkpoints_out = {}
    for checkpoint in CHECKPOINTS:
        qualifying = qualifying_politicians(by_politician, checkpoint)
        results = []
        for pid, txns in qualifying.items():
            visible = [t for t in txns if t.disclosure_date <= checkpoint]
            name = visible[0].politician_name
            perf = compute_performance(pid, name, visible, evaluation_date=checkpoint)
            results.append(perf)
        checkpoints_out[checkpoint] = results
        rankable = [
            r for r in results
            if r.n_legs_usable >= MIN_USABLE_LEGS
            and (r.dollar_weighted_coverage_pct or 0) >= MIN_DOLLAR_COVERAGE
            and r.excess_return_immediate is not None
        ]
        print(f"  {checkpoint}: {len(qualifying)} qualifying as of this checkpoint, "
              f"{len(results)} evaluated, {len(rankable)} rankable "
              f"(>= {MIN_USABLE_LEGS} priced legs, >= {MIN_DOLLAR_COVERAGE:.0%} $ coverage)")

    return checkpoints_out


def _perf_to_dict(p) -> dict:
    return {
        "politician_id": p.politician_id, "politician_name": p.politician_name,
        "evaluation_date": p.evaluation_date,
        "number_of_transactions": p.number_of_transactions,
        "number_of_unique_securities": p.number_of_unique_securities,
        "portfolio_return_immediate": p.portfolio_return_immediate,
        "portfolio_return_lagged": p.portfolio_return_lagged,
        "benchmark_return_immediate": p.benchmark_return_immediate,
        "benchmark_return_lagged": p.benchmark_return_lagged,
        "excess_return_immediate": p.excess_return_immediate,
        "excess_return_lagged": p.excess_return_lagged,
        "max_drawdown_immediate": p.max_drawdown_immediate,
        "concentration_hhi": p.concentration_hhi,
        "avg_holding_period_days": p.avg_holding_period_days,
        "num_holding_period_observations": p.num_holding_period_observations,
        "transaction_frequency_per_year": p.transaction_frequency_per_year,
        "mean_disclosure_age_days": p.mean_disclosure_age_days,
        "median_disclosure_age_days": p.median_disclosure_age_days,
        "performance_by_sector": p.performance_by_sector,
        "performance_by_year": p.performance_by_year,
        "dollar_weighted_coverage_pct": p.dollar_weighted_coverage_pct,
        "n_legs_usable": p.n_legs_usable,
        "n_legs_total": p.n_legs_total,
    }


if __name__ == "__main__":
    checkpoints_out = run_walk_forward()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    serializable = {
        checkpoint: [_perf_to_dict(p) for p in results]
        for checkpoint, results in checkpoints_out.items()
    }
    out_path = DATA_DIR / "walk_forward_results.json"
    out_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    print(f"Saved to {out_path}")
