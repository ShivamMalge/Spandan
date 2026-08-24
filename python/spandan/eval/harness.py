"""`make eval`. Selects a threshold on validation, then reports on test.

The one rule this module exists to enforce: **the test window is read exactly
once, at the end, with every parameter already fixed.** Threshold selection runs
against the validation window carved out of the training period, and the chosen
value is printed with its provenance so the claim is checkable rather than
promised.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..detect import DetectorConfig, ReferenceDetector
from ..gen.build import MANIFEST_FILENAME
from ..gen.schema import ATTACK_SCENARIOS, Event
from . import metrics
from .costs import CostModel, compute_costs, reweight_to_prevalence
from .loader import Split, load_split

def _use_utf8_stdout() -> str:
    """Windows consoles default to cp1252, which cannot encode the rupee sign.

    Reconfigure rather than degrade: the rupee figure is the centrepiece of this
    report and `Rs` in a screenshot reads like a workaround. Falls back to `Rs`
    only if the stream genuinely cannot be reconfigured.
    """
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            return "Rs "
    return "₹"


R = _use_utf8_stdout()


def rupees(paise: float) -> str:
    value = paise / 100.0
    if abs(value) >= 1e7:
        return f"{R}{value/1e7:,.2f}Cr"
    if abs(value) >= 1e5:
        return f"{R}{value/1e5:,.2f}L"
    return f"{R}{value:,.0f}"


def score_stream(events: list[Event], config: DetectorConfig) -> np.ndarray:
    return ReferenceDetector(config).score_batch(events)


def score_with_warmup(
    warmup: list[Event], target: list[Event], config: DetectorConfig
) -> np.ndarray:
    """Warm the detector on earlier events, then score the target window.

    Without this, threshold selection would be measuring cold-start behaviour:
    every entity's baseline would be empty for the first stretch of the window
    and the scores would be an artifact of that rather than of the traffic.
    """
    detector = ReferenceDetector(config)
    detector.score_batch(warmup)
    return detector.score_batch(target)


# --- threshold selection, on validation only --------------------------------


def sweep_thresholds(
    events: list[Event],
    scores: np.ndarray,
    model: CostModel,
    count: int = 60,
) -> list[dict]:
    """Net rupee position across the full threshold range.

    This is what replaced the calibration curve. The score is a deviation from a
    per-entity baseline rather than a probability, so calibration would answer a
    question nobody asked; the sweep answers the question that was asked, which
    is why this threshold and not another one.
    """
    lo = float(np.percentile(scores, 50.0))
    hi = float(np.max(scores))
    grid = np.linspace(lo, hi, count)

    rows = []
    for threshold in grid:
        alert_list = metrics.alerts(events, scores, threshold)
        breakdown = compute_costs(events, scores, threshold, model, len(alert_list))
        confusion = metrics.confusion_at(scores, np.array([e.label for e in events]), threshold)
        rows.append(
            {
                "threshold": float(threshold),
                "gross_paise": breakdown.gross_paise,
                "net_paise": breakdown.net_paise(model.assumed_review_paise),
                "alerts": len(alert_list),
                "precision": confusion.precision,
                "recall": confusion.recall,
                "break_even_paise": breakdown.break_even_review_paise(),
            }
        )
    return rows


#: Two operating points whose net positions are within this of each other are
#: treated as economically equivalent, and the tie is broken toward recall.
NET_EQUIVALENCE_BAND = 0.05


def select_threshold(rows: list[dict]) -> dict:
    """Maximum net rupee position, with ties broken toward recall.

    Chosen on rupees rather than F1, because the project's stated bar is a
    false-positive cost in rupees. If that produces poor recall, that is the
    finding and it gets reported.

    The tie-break exists because the net curve is bumpy — alert counts move in
    steps, so neighbouring thresholds can differ by a few percent for reasons
    that are noise rather than signal. Taking the raw argmax would chase those
    steps. Among operating points within `NET_EQUIVALENCE_BAND` of the best, the
    lowest threshold wins: if two points make the merchant the same money, the
    one that catches more attacks is the better product.

    This is a selection rule, not a tuned parameter, and it runs on the
    validation window like everything else.
    """
    best = max(row["net_paise"] for row in rows)
    if best <= 0:
        return max(rows, key=lambda row: row["net_paise"])
    floor = best * (1.0 - NET_EQUIVALENCE_BAND)
    equivalent = [row for row in rows if row["net_paise"] >= floor]
    return min(equivalent, key=lambda row: row["threshold"])


# --- the report --------------------------------------------------------------


def evaluate(
    split: Split,
    config: DetectorConfig,
    model: CostModel,
    episode_windows: list[dict],
) -> dict:
    validation_scores = score_with_warmup(split.train_warmup, split.validation, config)
    sweep = sweep_thresholds(split.validation, validation_scores, model)
    chosen = select_threshold(sweep)
    threshold = chosen["threshold"]

    test_scores = score_with_warmup(split.train, split.test, config)
    labels = np.array([e.label for e in split.test])

    confusion = metrics.confusion_at(test_scores, labels, threshold)
    alert_list = metrics.alerts(split.test, test_scores, threshold)
    breakdown = compute_costs(split.test, test_scores, threshold, model, len(alert_list))
    reweighted = reweight_to_prevalence(
        confusion.tp, confusion.fp, confusion.fn, confusion.tn, model.target_prevalence
    )

    clean_scores = [test_scores[i] for i, e in enumerate(split.test) if e.label == 0]
    highest_clean = float(max(clean_scores)) if clean_scores else 0.0
    margin = {
        "highest_clean_score": highest_clean,
        "threshold": threshold,
        "headroom": threshold - highest_clean,
        "headroom_pct": (threshold - highest_clean) / threshold if threshold else 0.0,
        "highest_clean_scenario": max(
            ((test_scores[i], e.scenario_id) for i, e in enumerate(split.test) if e.label == 0),
            default=(0.0, "-"),
        )[1],
    }

    return {
        "margin": margin,
        "threshold": threshold,
        "threshold_source": {
            "window": "validation",
            "start_ms": split.validation_start_ms,
            "end_ms": max(e.ts for e in split.validation),
            "events": len(split.validation),
            "criterion": "max net rupee position on the sweep",
        },
        "confusion": confusion,
        "average_precision": average_precision_of(test_scores, labels),
        "per_scenario": metrics.per_scenario_counts(split.test, test_scores, threshold),
        "time_to_detection": metrics.time_to_detection(
            split.test, test_scores, threshold, episode_windows
        ),
        "alerts": alert_list,
        "alerts_per_day": metrics.alerts_per_day(split.test, alert_list),
        "costs": breakdown,
        "reweighted": reweighted,
        "sweep": sweep,
        "validation_scores": validation_scores,
        "test_scores": test_scores,
    }


def average_precision_of(scores: np.ndarray, labels: np.ndarray) -> float:
    return metrics.average_precision(scores, labels)


def _bar(value: float, peak: float, width: int = 26) -> str:
    if peak <= 0:
        return " " * width
    filled = int(round(width * max(value, 0.0) / peak))
    return "#" * filled + "." * (width - filled)


def render(result: dict, model: CostModel, split: Split) -> None:
    threshold = result["threshold"]
    source = result["threshold_source"]
    confusion = result["confusion"]
    costs = result["costs"]
    reweighted = result["reweighted"]

    print("=" * 78)
    print("THRESHOLD PROVENANCE")
    print("=" * 78)
    print(
        f"chosen on the {source['window']} window "
        f"[{source['start_ms']}, {source['end_ms']}], {source['events']} events"
    )
    print(f"criterion: {source['criterion']}")
    print(f"threshold: {threshold:.4f}")
    print("the test window was not read until this value was fixed.")

    print()
    print("=" * 78)
    print("METRICS ON THE TEMPORAL TEST SET")
    print("=" * 78)
    print(f"{'precision':<14}{confusion.precision:>10.4f}")
    print(f"{'recall':<14}{confusion.recall:>10.4f}")
    print(f"{'F1':<14}{confusion.f1:>10.4f}")
    print(f"{'PR-AUC':<14}{result['average_precision']:>10.4f}")
    print(
        f"{'confusion':<14}"
        f"TP={confusion.tp}  FP={confusion.fp}  FN={confusion.fn}  TN={confusion.tn}"
    )

    margin = result["margin"]
    print()
    print("-- how comfortable is that precision " + "-" * 41)
    if confusion.fp == 0:
        print("FP=0 is not a result to report on its own. What matters is the headroom:")
    print(
        f"highest-scoring CLEAN event  {margin['highest_clean_score']:>8.2f} "
        f"({margin['highest_clean_scenario']})"
    )
    print(f"threshold                    {margin['threshold']:>8.2f}")
    print(
        f"headroom                     {margin['headroom']:>8.2f}  "
        f"({margin['headroom_pct']:.1%} of the threshold)"
    )
    if margin["headroom_pct"] < 0.25:
        print("that is a narrow separation. Read the multi-seed spread below before")
        print("treating this precision as stable - one unlucky stream closes this gap.")

    print()
    print("-- precision at a stated prevalence " + "-" * 42)
    print("recall is prevalence-independent; precision is not.")
    print(
        f"observed prevalence  {reweighted.observed_prevalence:>8.4%}   "
        f"precision {reweighted.precision_observed:>7.4f}   (the generator's rate)"
    )
    print(
        f"target prevalence    {reweighted.target_prevalence:>8.4%}   "
        f"precision {reweighted.precision_target:>7.4f}   (ASSUMPTION, see costs.toml)"
    )
    print(
        f"negatives rescaled x{reweighted.negative_scale:,.1f} -> "
        f"effective FP {reweighted.effective_fp:,.0f};  "
        f"recall unchanged at {reweighted.recall:.4f}"
    )

    print()
    print("-- per scenario " + "-" * 62)
    print(f"{'scenario':<16}{'events':>9}{'flagged':>9}{'rate':>9}   what the rate means")
    for name, row in result["per_scenario"].items():
        meaning = "recall" if row["is_attack"] else "FALSE POSITIVES"
        print(
            f"{name:<16}{row['events']:>9}{row['flagged']:>9}{row['rate']:>9.4f}   {meaning}"
        )

    print()
    print("-- episode detection and time to detection " + "-" * 35)
    print("event-level recall understates the operational question: an episode has to")
    print("be caught ONCE to be acted on. Both are reported; neither replaces the other.")
    print(f"{'scenario':<14}{'episodes':>9}{'caught':>8}{'med events':>12}{'p90 events':>12}{'med rupees':>13}")
    for name in ATTACK_SCENARIOS:
        row = result["time_to_detection"].get(name)
        if not row:
            continue
        med = row["median_events"]
        p90 = row["p90_events"]
        rup = row["median_rupees"]
        caught = row["episodes"] - row["missed"]
        caught_cell = f"{caught}/{row['episodes']}"
        print(
            f"{name:<14}{row['episodes']:>9}{caught_cell:>8}"
            f"{('never' if math.isinf(med) else f'{med:.0f}'):>12}"
            f"{('never' if math.isinf(p90) else f'{p90:.0f}'):>12}"
            f"{('-' if math.isinf(rup) else f'{R}{rup:,.0f}'):>13}"
        )

    print()
    print("=" * 78)
    print("FALSE-POSITIVE COST, BY NEGATIVE CONTROL")
    print("=" * 78)
    print("the two controls attack different axes and are not averaged together")
    print(f"{'control':<16}{'flagged':>9}{'blocked-good cost':>20}   axis")
    for name, axis in (("flash_sale", "volume"), ("issuer_outage", "decline ratio")):
        flagged = costs.per_scenario_flagged.get(name, 0)
        cost = costs.per_scenario_blocked_good_paise.get(name, 0.0)
        print(f"{name:<16}{flagged:>9}{rupees(cost):>20}   {axis}")
    benign_flagged = costs.per_scenario_flagged.get("benign", 0)
    benign_cost = costs.per_scenario_blocked_good_paise.get("benign", 0.0)
    print(f"{'benign':<16}{benign_flagged:>9}{rupees(benign_cost):>20}   ordinary traffic")
    print()
    print(
        f"of {costs.blocked_good_events} blocked clean transactions, "
        f"{costs.blocked_good_events_that_would_decline} were going to decline anyway "
        "and cost the merchant no margin"
    )

    print()
    print("=" * 78)
    print("RUPEE POSITION")
    print("=" * 78)
    print(f"{'saved authorization fees':<34}{rupees(costs.saved_auth_fees_paise):>16}")
    print(f"{'avoided chargeback exposure':<34}{rupees(costs.avoided_chargebacks_paise):>16}")
    print(f"{'blocked good transactions':<34}{rupees(-costs.blocked_good_paise):>16}")
    print(f"{'':-<50}")
    print(f"{'GROSS (before review cost)':<34}{rupees(costs.gross_paise):>16}")
    print()
    breakeven = costs.break_even_review_paise()
    print(f"{'alerts a human must review':<34}{costs.alerts:>16}")
    print(f"{'alerts per day':<34}{result['alerts_per_day']:>16.1f}")
    print()
    print("BREAK-EVEN REVIEW COST -- the defensible figure, because it is an output:")
    if math.isinf(breakeven):
        print("  no review cost makes this unprofitable (no alerts raised)")
    else:
        print(f"  net stays positive while an analyst review costs under "
              f"{rupees(breakeven)} per alert")
    print(
        f"  for reference, at the assumed {rupees(model.assumed_review_paise)}/alert "
        f"(an ASSUMPTION, see costs.toml) net = "
        f"{rupees(costs.net_paise(model.assumed_review_paise))}"
    )


def render_sweep(result: dict, model: CostModel) -> None:
    print()
    print("=" * 78)
    print("COST vs THRESHOLD (on validation; this is why that threshold)")
    print("=" * 78)
    rows = result["sweep"]
    peak = max(row["net_paise"] for row in rows)
    chosen = result["threshold"]
    print(f"{'threshold':>10}{'net':>14}{'alerts':>8}{'prec':>7}{'recall':>8}  net position")
    for row in rows[:: max(1, len(rows) // 22)]:
        marker = " <== CHOSEN" if abs(row["threshold"] - chosen) < 1e-9 else ""
        print(
            f"{row['threshold']:>10.2f}{rupees(row['net_paise']):>14}{row['alerts']:>8}"
            f"{row['precision']:>7.3f}{row['recall']:>8.3f}  "
            f"{_bar(row['net_paise'], peak)}{marker}"
        )


def render_ablations(rows: list[dict]) -> None:
    print()
    print("=" * 78)
    print("ABLATIONS")
    print("=" * 78)
    print("two, not four: drop-Welford and drop-per-device were cut on Aug 24 to fund")
    print("the issuer-outage control. These are the two a payments panel asks about.")
    print(f"{'variant':<22}{'precision':>11}{'recall':>9}{'PR-AUC':>9}{'net':>14}{'alerts':>8}")
    for row in rows:
        print(
            f"{row['name']:<22}{row['precision']:>11.4f}{row['recall']:>9.4f}"
            f"{row['pr_auc']:>9.4f}{rupees(row['net_paise']):>14}{row['alerts']:>8}"
        )


def render_multiseed(rows: list[dict]) -> None:
    print()
    print("=" * 78)
    print("MULTI-SEED STABILITY")
    print("=" * 78)
    if len(rows) < 2:
        print("single seed only (pass SEEDS=3 for the spread)")
        return
    print("if the spread is wide, the headline numbers are noise and should not be")
    print("reported as if they were stable.")
    print(f"{'metric':<20}{'min':>12}{'median':>12}{'max':>12}{'spread':>12}")
    for key, label, fmt in (
        ("precision", "precision", "{:.4f}"),
        ("recall", "recall", "{:.4f}"),
        ("pr_auc", "PR-AUC", "{:.4f}"),
        ("net_rupees", "net rupees", "{:,.0f}"),
        ("alerts_per_day", "alerts/day", "{:.1f}"),
    ):
        values = [row[key] for row in rows]
        lo, mid, hi = min(values), float(np.median(values)), max(values)
        print(
            f"{label:<20}{fmt.format(lo):>12}{fmt.format(mid):>12}{fmt.format(hi):>12}"
            f"{fmt.format(hi - lo):>12}"
        )
    print()
    print("per seed - this is the diagnostic that says WHERE the instability is:")
    print(f"{'seed':>10}{'threshold':>11}{'PR-AUC':>9}{'precision':>11}{'recall':>9}")
    for row in rows:
        print(
            f"{row['seed']:>10}{row['threshold']:>11.2f}{row['pr_auc']:>9.4f}"
            f"{row['precision']:>11.4f}{row['recall']:>9.4f}"
        )
    print()
    pr_spread = max(r["pr_auc"] for r in rows) - min(r["pr_auc"] for r in rows)
    th_values = [r["threshold"] for r in rows]
    print(
        "PR-AUC is threshold-free, so its spread "
        f"({pr_spread:.4f}) is the detector and the data, not the operating point."
    )
    print(
        f"selected thresholds ranged {min(th_values):.2f} to {max(th_values):.2f}, so "
        "threshold selection adds instability on top of that."
    )


def render_verdict(result: dict, seed_rows: list[dict], model: CostModel) -> None:
    """The honest summary, after the spread is known.

    Printed last and deliberately not optional. A single-seed headline of
    "precision 1.00" is the kind of number this project exists not to publish.
    """
    print()
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    confusion = result["confusion"]
    if len(seed_rows) < 2:
        print("single seed. Run with SEEDS=3 before believing any of the above.")
        return

    precisions = [r["precision"] for r in seed_rows]
    recalls = [r["recall"] for r in seed_rows]
    nets = [r["net_rupees"] for r in seed_rows]
    spread_p = max(precisions) - min(precisions)
    spread_r = max(recalls) - min(recalls)

    print(f"headline seed reports precision {confusion.precision:.4f}, recall {confusion.recall:.4f}.")
    print(
        f"across {len(seed_rows)} streams that is precision "
        f"{min(precisions):.2f}-{max(precisions):.2f} and recall "
        f"{min(recalls):.2f}-{max(recalls):.2f}, "
        f"net {R}{min(nets):,.0f}-{R}{max(nets):,.0f}."
    )
    print()
    if spread_p > 0.15 or spread_r > 0.15:
        print("*** THE HEADLINE NUMBERS ARE NOT STABLE. ***")
        print("Reporting the single-seed figures as the result would be reporting noise.")
        print("Report the median and the range, and read docs/FAILURE_MODES.md for why.")
        print()
        print("Two separate causes, and they need different fixes:")
        print("  1. the test window holds 6 attack episodes (2 per scenario), so any")
        print("     per-scenario recall rests on a sample of two. That is an")
        print("     underpowered evaluation regardless of which way it errs.")
        print("  2. threshold selection chases a bumpy net curve across streams.")
    else:
        print("spread is narrow enough that the headline figures can be reported as-is,")
        print("with the range quoted alongside them.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Spandan on the temporal test set.")
    parser.add_argument("--data", default="data")
    parser.add_argument("--seeds", type=int, default=3, help="generator seeds for the spread")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(argv)

    data_dir = Path(args.data)
    manifest = json.loads((data_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    episode_windows = manifest["episode_windows"]

    model = CostModel.load()
    config = DetectorConfig()
    split = load_split(data_dir)

    print(f"data          {data_dir.resolve()}")
    print(f"seed          {manifest['seed']}")
    print(f"config hash   {manifest['config_hash'][:16]}")
    print(split.describe())
    print()

    result = evaluate(split, config, model, episode_windows)
    render(result, model, split)
    render_sweep(result, model)

    ablation_rows = run_ablations(split, config, model, result["threshold"])
    render_ablations(ablation_rows)

    seed_rows = run_multiseed(args.seeds, manifest["seed"], config, model)
    render_multiseed(seed_rows)
    render_verdict(result, seed_rows, model)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(summarise(result, ablation_rows, seed_rows), indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return 0


def run_ablations(
    split: Split, config: DetectorConfig, model: CostModel, _threshold: float
) -> list[dict]:
    variants = [
        ("full", config),
        ("drop-EWMA", replace(config, use_ewma=False)),
        ("drop-per-IP", replace(config, use_per_ip=False)),
    ]
    rows = []
    for name, variant in variants:
        validation_scores = score_with_warmup(split.train_warmup, split.validation, variant)
        sweep = sweep_thresholds(split.validation, validation_scores, model)
        chosen = select_threshold(sweep)["threshold"]

        test_scores = score_with_warmup(split.train, split.test, variant)
        labels = np.array([e.label for e in split.test])
        confusion = metrics.confusion_at(test_scores, labels, chosen)
        alert_list = metrics.alerts(split.test, test_scores, chosen)
        breakdown = compute_costs(split.test, test_scores, chosen, model, len(alert_list))
        rows.append(
            {
                "name": name,
                "precision": confusion.precision,
                "recall": confusion.recall,
                "pr_auc": metrics.average_precision(test_scores, labels),
                "net_paise": breakdown.net_paise(model.assumed_review_paise),
                "alerts": len(alert_list),
            }
        )
    return rows


def run_multiseed(
    seed_count: int, base_seed: int, config: DetectorConfig, model: CostModel
) -> list[dict]:
    """Re-run the whole evaluation on freshly generated streams.

    Generates into a temp directory each time. Not a resample of one dataset —
    a different stream, so the spread reflects generator variance rather than
    sampling variance within a fixed draw.
    """
    import tempfile

    from ..gen.build import build
    from ..gen.config import DEFAULT_CONFIG

    rows = []
    for offset in range(max(seed_count, 1)):
        seed = base_seed + offset
        with tempfile.TemporaryDirectory() as tmp:
            gen_config = replace(DEFAULT_CONFIG, seed=seed)
            manifest = build(gen_config, tmp)
            split = load_split(tmp)
            result = evaluate(split, config, model, manifest["episode_windows"])
            rows.append(
                {
                    "seed": seed,
                    "threshold": result["threshold"],
                    "precision": result["confusion"].precision,
                    "recall": result["confusion"].recall,
                    "pr_auc": result["average_precision"],
                    "net_rupees": result["costs"].net_paise(model.assumed_review_paise) / 100.0,
                    "alerts_per_day": result["alerts_per_day"],
                }
            )
    return rows


def summarise(result: dict, ablations: list[dict], seeds: list[dict]) -> dict:
    confusion = result["confusion"]
    costs = result["costs"]
    reweighted = result["reweighted"]
    return {
        "threshold": result["threshold"],
        "precision": confusion.precision,
        "recall": confusion.recall,
        "f1": confusion.f1,
        "pr_auc": result["average_precision"],
        "confusion": {"tp": confusion.tp, "fp": confusion.fp, "fn": confusion.fn, "tn": confusion.tn},
        "precision_at_target_prevalence": reweighted.precision_target,
        "target_prevalence": reweighted.target_prevalence,
        "gross_rupees": costs.gross_paise / 100.0,
        "break_even_review_rupees": costs.break_even_review_paise() / 100.0,
        "alerts": costs.alerts,
        "alerts_per_day": result["alerts_per_day"],
        "per_scenario": result["per_scenario"],
        "ablations": [{k: v for k, v in row.items()} for row in ablations],
        "multiseed": seeds,
    }


if __name__ == "__main__":
    raise SystemExit(main())
