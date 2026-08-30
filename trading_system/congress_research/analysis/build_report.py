"""Assembles the final report dataset: rankings, consistency metrics, and
dollar-weighted aggregate sector/year breakdowns, all computed from the
walk-forward results (never re-introducing lookahead by peeking past the
latest checkpoint for anything the report presents as "known as of
today").

Nothing produced here should be read as evidence that any politician
used material nonpublic information, or as a prediction that whoever
ranks well now will continue to. The ranking key is deliberately the
LAGGED (disclosure-date-priced) excess return, not the immediate
(transaction-date-priced) one - see rankable_at()."""

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from performance_engine import _build_legs, _weighted_mean
from walk_forward import (
    CHECKPOINTS, MIN_DOLLAR_COVERAGE, MIN_USABLE_LEGS,
    load_all_transactions_by_politician, qualifying_politicians,
)

DATA_DIR = Path(__file__).resolve().parent / "data"
LATEST_CHECKPOINT = CHECKPOINTS[-1]


def is_rankable(p: dict) -> bool:
    return (
        p["n_legs_usable"] >= MIN_USABLE_LEGS
        and (p["dollar_weighted_coverage_pct"] or 0) >= MIN_DOLLAR_COVERAGE
        and p["excess_return_immediate"] is not None
        and p["excess_return_lagged"] is not None
    )


def rankable_at(results_by_checkpoint: dict, checkpoint: str) -> list:
    """Ranked by LAGGED excess return - priced at disclosure date, the
    realistic figure for anyone who could only act once a trade became
    public. Immediate (pre-disclosure-timing) excess return is still
    computed and shown alongside every row, but deliberately isn't the
    sort/gate key: that's the metric closest to measuring a pre-
    disclosure informational advantage, and using it as the headline
    ranking risks reading as exactly the kind of claim this report must
    not make."""
    rows = [p for p in results_by_checkpoint[checkpoint] if is_rankable(p)]
    rows.sort(key=lambda p: p["excess_return_lagged"], reverse=True)
    return rows


def consistency_metrics(results_by_checkpoint: dict) -> dict:
    transitions = []
    for i in range(len(CHECKPOINTS) - 1):
        a, b = rankable_at(results_by_checkpoint, CHECKPOINTS[i]), rankable_at(results_by_checkpoint, CHECKPOINTS[i + 1])
        top_a = {p["politician_id"] for p in a[:10]}
        top_b = {p["politician_id"] for p in b[:10]}
        transitions.append({
            "from": CHECKPOINTS[i], "to": CHECKPOINTS[i + 1],
            "top10_overlap": len(top_a & top_b), "top10_size_from": len(top_a), "top10_size_to": len(top_b),
        })

    first, last = rankable_at(results_by_checkpoint, CHECKPOINTS[0]), rankable_at(results_by_checkpoint, LATEST_CHECKPOINT)
    first_rank = {p["politician_id"]: i for i, p in enumerate(first)}
    last_rank = {p["politician_id"]: i for i, p in enumerate(last)}
    common = sorted(set(first_rank) & set(last_rank))
    rank_correlation = None
    if len(common) > 3:
        xs = [first_rank[pid] for pid in common]
        ys = [last_rank[pid] for pid in common]
        mean_x, mean_y = statistics.mean(xs), statistics.mean(ys)
        cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        sx = sum((x - mean_x) ** 2 for x in xs) ** 0.5
        sy = sum((y - mean_y) ** 2 for y in ys) ** 0.5
        rank_correlation = (cov / (sx * sy)) if sx > 0 and sy > 0 else None

    return {
        "transitions": transitions,
        "first_vs_last_common_n": len(common),
        "first_vs_last_rank_correlation": rank_correlation,
        "first_checkpoint": CHECKPOINTS[0], "last_checkpoint": LATEST_CHECKPOINT,
    }


def aggregate_breakdown(qualifying: dict) -> dict:
    """Dollar-weighted sector/year performance across every qualifying
    politician's transactions, computed fresh from the raw legs (not
    re-derived from per-politician summaries, so it can be weighted
    correctly by actual disclosed dollar amount)."""
    all_legs = []
    for pid, txns in qualifying.items():
        visible = [t for t in txns if t.disclosure_date <= LATEST_CHECKPOINT]
        by_ticker = {}
        for t in visible:
            if t.transaction_type in ("Purchase", "Sale (Full)", "Sale (Partial)") and t.ticker:
                by_ticker.setdefault(t.ticker, []).append(t)
        for ticker_txns in by_ticker.values():
            ticker_txns.sort(key=lambda t: (t.transaction_date, t.id))
        legs = _build_legs(by_ticker, LATEST_CHECKPOINT, lagged=False)
        all_legs.extend(l for l in legs if l.usable and l.ret is not None)

    by_sector, by_year = {}, {}
    for leg in all_legs:
        by_sector.setdefault(leg.sector, []).append(leg)
        by_year.setdefault(leg.year, []).append(leg)

    def summarize(groups: dict) -> dict:
        out = {}
        for key, legs in groups.items():
            ret = _weighted_mean((l.ret, l.dollar_amount) for l in legs)
            bench = _weighted_mean((l.benchmark_ret, l.dollar_amount) for l in legs)
            excess = (ret - bench) if (ret is not None and bench is not None) else None
            out[key] = {"return": ret, "benchmark_return": bench, "excess_return": excess, "n_legs": len(legs),
                        "total_dollar_amount": sum(l.dollar_amount for l in legs)}
        return out

    from sector_map import FUND_OR_TREASURY
    by_sector.pop(FUND_OR_TREASURY, None)
    return {"by_sector": summarize(by_sector), "by_year": summarize(by_year), "total_legs": len(all_legs)}


def universe_coverage_stats(qualifying: dict) -> dict:
    """How much of what was actually traded has price data at all - the
    'rankable' population is a mechanical proxy for 'trades large,
    liquid, well-covered names', not a proxy for trading skill, and that
    should be visible as a number, not just a methodology footnote."""
    import price_lookup

    traded_tickers = set()
    for txns in qualifying.values():
        for t in txns:
            if t.ticker and t.disclosure_date <= LATEST_CHECKPOINT and t.transaction_type in ("Purchase", "Sale (Full)", "Sale (Partial)"):
                traded_tickers.add(t.ticker)
    priced_tickers = {t for t in traded_tickers if price_lookup.has_price_data(t)}
    return {
        "distinct_tickers_traded": len(traded_tickers),
        "distinct_tickers_priced": len(priced_tickers),
        "priced_ticker_coverage_pct": (len(priced_tickers) / len(traded_tickers)) if traded_tickers else None,
    }


def amount_bracket_skew(qualifying: dict) -> dict:
    """What fraction of all disclosed buy/sell transactions fall in the
    lowest STOCK Act bracket ($1,001-$15,000) - by far the widest
    bracket relative to its own floor, so if real amounts cluster near
    the floor (as commonly suspected), the range-midpoint approximation
    overstates dollar size most for the majority of transactions, not
    uniformly across the board."""
    total, lowest_bracket = 0, 0
    for txns in qualifying.values():
        for t in txns:
            if t.transaction_type not in ("Purchase", "Sale (Full)", "Sale (Partial)") or t.disclosure_date > LATEST_CHECKPOINT:
                continue
            total += 1
            if t.amount_range_low == 1001 and t.amount_range_high == 15000:
                lowest_bracket += 1
    return {
        "total_transactions": total, "lowest_bracket_count": lowest_bracket,
        "lowest_bracket_pct": (lowest_bracket / total) if total else None,
    }


def main():
    with (DATA_DIR / "walk_forward_results.json").open(encoding="utf-8") as f:
        results_by_checkpoint = json.load(f)

    by_politician = load_all_transactions_by_politician()
    qualifying = qualifying_politicians(by_politician, LATEST_CHECKPOINT)

    final_ranking = rankable_at(results_by_checkpoint, LATEST_CHECKPOINT)
    consistency = consistency_metrics(results_by_checkpoint)
    aggregate = aggregate_breakdown(qualifying)
    universe_coverage = universe_coverage_stats(qualifying)
    bracket_skew = amount_bracket_skew(qualifying)

    out = {
        "latest_checkpoint": LATEST_CHECKPOINT,
        "universe_coverage": universe_coverage,
        "amount_bracket_skew": bracket_skew,
        "min_transactions": 5, "min_usable_legs": MIN_USABLE_LEGS, "min_dollar_coverage": MIN_DOLLAR_COVERAGE,
        "n_politicians_total": len(by_politician),
        "n_politicians_qualifying": len(qualifying),
        "n_politicians_rankable": len(final_ranking),
        "final_ranking": final_ranking,
        "consistency": consistency,
        "aggregate_breakdown": aggregate,
        "checkpoints": CHECKPOINTS,
        "results_by_checkpoint": results_by_checkpoint,
    }
    out_path = DATA_DIR / "report_data.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Saved report data to {out_path}")
    print(f"Rankable politicians at {LATEST_CHECKPOINT}: {len(final_ranking)}")
    print(f"First-vs-last rank correlation: {consistency['first_vs_last_rank_correlation']}")


if __name__ == "__main__":
    main()
