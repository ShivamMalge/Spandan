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
from ..gen.schema import ATTACK_SCENARIOS, NEGATIVE_CONTROLS, Event
from . import metrics
from .costs import CostModel, compute_costs, gross_at, reweight_to_prevalence
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

#: Which axis each negative control attacks. Used so the false-positive table is
#: driven by the schema rather than by a hand-written pair - the single-merchant
#: control was added and silently missed the table on its first run, which is the
#: precise failure this dict prevents.
CONTROL_AXES = {
    "flash_sale": "volume",
    "issuer_outage": "decline ratio",
    "outage_single_merchant": "decline ratio, no crutches",
}


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


def score_split_once(split: Split, config: DetectorConfig) -> tuple[np.ndarray, np.ndarray]:
    """Validation and test scores from a SINGLE pass over the whole stream.

    The detector is causal and processes events in order, so feeding
    warmup -> validation -> test through one state machine produces exactly the
    scores that two separate warmed passes produce. Doing it once instead of
    twice removes about 40% of the evaluation's runtime, which matters now that
    the stream is 100 days rather than 14.

    `test_single_pass_matches_two_pass_scoring` asserts the equivalence rather
    than assuming it - this is the kind of optimisation that is easy to get
    subtly wrong and impossible to notice afterwards.
    """
    detector = ReferenceDetector(config)
    detector.score_batch(split.train_warmup)
    validation_scores = detector.score_batch(split.validation)
    test_scores = detector.score_batch(split.test)
    return validation_scores, test_scores


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
    lo = float(np.percentile(scores, 75.0))
    hi = float(np.max(scores))
    grid = np.linspace(lo, hi, count)

    # Everything at or below the lowest threshold on the grid is invisible to
    # every row, so the sweep works on the candidate subset. The per-threshold
    # arithmetic is then vectorised - the per-event Python loops cost ~100s per
    # sweep on a 100-day stream and dominated `make eval`.
    keep = np.flatnonzero(scores > lo)
    candidates = [events[i] for i in keep]
    candidate_scores = scores[keep]
    pre = metrics.SweepPrecompute(candidates, candidate_scores)
    labels = np.array([e.label for e in events])

    rows = []
    for threshold in grid:
        alerts = metrics.alert_count_at(pre, threshold)
        gross = gross_at(pre, threshold, model)
        confusion = metrics.confusion_at(scores, labels, threshold)
        rows.append(
            {
                "threshold": float(threshold),
                "gross_paise": gross,
                "net_paise": gross - alerts * model.assumed_review_paise,
                "alerts": alerts,
                "precision": confusion.precision,
                "recall": confusion.recall,
                "break_even_paise": (gross / alerts) if alerts else 0.0,
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
    validation_scores, test_scores = score_split_once(split, config)
    sweep = sweep_thresholds(split.validation, validation_scores, model)
    chosen = select_threshold(sweep)
    threshold = chosen["threshold"]

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
    print("DETECTION SPEED  -- the number closest to the product")
    print("=" * 78)
    print("A spike detector is judged on how fast it catches an episode, not only on")
    print("whether it eventually does. Attempt 40 and attempt 400 are both recall=1 and")
    print("are not the same outcome for the merchant. An episode has to be caught ONCE")
    print("to be acted on, so episode detection and event recall measure different")
    print("things and both are reported.")
    print()
    print(f"{'scenario':<14}{'episodes':>9}{'caught':>9}{'med events':>12}{'p90 events':>12}{'med rupees':>13}")
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
            f"{name:<14}{row['episodes']:>9}{caught_cell:>9}"
            f"{('never' if math.isinf(med) else f'{med:.0f}'):>12}"
            f"{('never' if math.isinf(p90) else f'{p90:.0f}'):>12}"
            f"{('-' if math.isinf(rup) else f'{R}{rup:,.0f}'):>13}"
        )
    print()
    print("`med events` = events into the episode before the first flag.")
    print("`med rupees` = value that went through before the first flag.")

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
    if margin["headroom"] < 0:
        print()
        print("HEADROOM IS NEGATIVE. The threshold sits BELOW the highest-scoring clean")
        print(f"event, so `{margin['highest_clean_scenario']}` is not being rejected at all -")
        print("it is being scored like an attack and separated only by where the line")
        print("happens to fall. This is a measured failure of the detector, not a margin.")
    elif margin["headroom_pct"] < 0.25:
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
    print(f"{'scenario':<24}{'events':>9}{'flagged':>9}{'rate':>9}   what the rate means")
    for name, row in result["per_scenario"].items():
        meaning = "recall" if row["is_attack"] else "FALSE POSITIVES"
        print(
            f"{name:<24}{row['events']:>9}{row['flagged']:>9}{row['rate']:>9.4f}   {meaning}"
        )


    print()
    print("=" * 78)
    print("FALSE-POSITIVE COST, BY NEGATIVE CONTROL")
    print("=" * 78)
    print("each control attacks a different axis; they are never averaged together")
    print(f"{'control':<24}{'events':>9}{'flagged':>9}{'rate':>8}{'blocked-good cost':>20}   axis")
    for name in NEGATIVE_CONTROLS:
        flagged = costs.per_scenario_flagged.get(name, 0)
        cost = costs.per_scenario_blocked_good_paise.get(name, 0.0)
        total = result["per_scenario"].get(name, {}).get("events", 0)
        rate = flagged / total if total else 0.0
        print(
            f"{name:<24}{total:>9}{flagged:>9}{rate:>8.4f}{rupees(cost):>20}   "
            f"{CONTROL_AXES.get(name, '')}"
        )
    benign_flagged = costs.per_scenario_flagged.get("benign", 0)
    benign_cost = costs.per_scenario_blocked_good_paise.get("benign", 0.0)
    benign_total = result["per_scenario"].get("benign", {}).get("events", 0)
    benign_rate = benign_flagged / benign_total if benign_total else 0.0
    print(
        f"{'benign':<24}{benign_total:>9}{benign_flagged:>9}{benign_rate:>8.4f}"
        f"{rupees(benign_cost):>20}   ordinary traffic"
    )
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

    rows = run_seed_matrix(args.seeds, manifest["seed"], config, model)
    render_ablation_matrix(rows, args.seeds)
    render_multiseed(rows)
    render_verdict(result, rows, model)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(summarise(result, rows), indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return 0


VARIANTS = ("full", "drop-EWMA", "drop-per-IP")


def variant_config(name: str, config: DetectorConfig) -> DetectorConfig:
    if name == "full":
        return config
    if name == "drop-EWMA":
        return replace(config, use_ewma=False)
    if name == "drop-per-IP":
        return replace(config, use_per_ip=False)
    raise ValueError(f"unknown variant {name}")


def run_seed_matrix(
    seed_count: int, base_seed: int, config: DetectorConfig, model: CostModel
) -> list[dict]:
    """Every variant on every seed.

    Phase 2 ran the ablations on a single stream, which was the gap in that
    report: with headline precision swinging 0.67-1.00 across seeds, a rupee gap
    between two variants measured on one stream is indistinguishable from that
    stream happening to disfavour one of them. An architectural claim needs the
    spread.

    One generation per seed, one scoring pass per (seed, variant).
    """
    import tempfile

    from ..gen.build import build
    from ..gen.config import default_config

    rows = []
    for offset in range(max(seed_count, 1)):
        seed = base_seed + offset
        with tempfile.TemporaryDirectory() as tmp:
            manifest = build(default_config(seed=seed), tmp)
            split = load_split(tmp)
            windows = manifest["episode_windows"]
            for name in VARIANTS:
                result = evaluate(split, variant_config(name, config), model, windows)
                confusion = result["confusion"]
                rows.append(
                    {
                        "seed": seed,
                        "variant": name,
                        "threshold": result["threshold"],
                        "precision": confusion.precision,
                        "recall": confusion.recall,
                        "pr_auc": result["average_precision"],
                        "net_rupees": result["costs"].net_paise(model.assumed_review_paise) / 100.0,
                        "alerts": result["costs"].alerts,
                        "alerts_per_day": result["alerts_per_day"],
                        "headroom_pct": result["margin"]["headroom_pct"],
                    }
                )
    return rows


def _spread(values: list[float]) -> tuple[float, float, float]:
    return min(values), float(np.median(values)), max(values)


def render_ablation_matrix(rows: list[dict], seed_count: int) -> None:
    print()
    print("=" * 78)
    print("ABLATIONS, ACROSS SEEDS")
    print("=" * 78)
    print("two ablations, not four: drop-Welford and drop-per-device were cut on Aug 24")
    print("to fund the issuer-outage control.")
    print()
    print(f"median across {seed_count} streams, with [min, max]:")
    print(f"{'variant':<14}{'precision':>22}{'recall':>22}{'net rupees':>24}")
    for name in VARIANTS:
        subset = [row for row in rows if row["variant"] == name]
        if not subset:
            continue
        p_lo, p_mid, p_hi = _spread([r["precision"] for r in subset])
        r_lo, r_mid, r_hi = _spread([r["recall"] for r in subset])
        n_lo, n_mid, n_hi = _spread([r["net_rupees"] for r in subset])
        print(
            f"{name:<14}"
            f"{f'{p_mid:.3f} [{p_lo:.2f},{p_hi:.2f}]':>22}"
            f"{f'{r_mid:.3f} [{r_lo:.2f},{r_hi:.2f}]':>22}"
            f"{f'{n_mid:,.0f} [{n_lo:,.0f},{n_hi:,.0f}]':>24}"
        )

    full = [r for r in rows if r["variant"] == "full"]
    print()
    print("does any ablation gap survive the spread? paired per seed, which is the")
    print("only comparison that controls for the stream:")
    for name in VARIANTS[1:]:
        variant = [r for r in rows if r["variant"] == name]
        deltas = [
            v["net_rupees"] - f["net_rupees"]
            for f, v in zip(full, variant)
            if f["seed"] == v["seed"]
        ]
        if not deltas:
            continue
        wins = sum(1 for d in deltas if d > 0)
        lo, mid, hi = _spread(deltas)
        verdict = "CONSISTENT" if wins in (0, len(deltas)) else "NOT consistent across seeds"
        print(
            f"  {name:<14} net delta vs full: median {R}{mid:,.0f} "
            f"[{R}{lo:,.0f}, {R}{hi:,.0f}]  beats full on {wins}/{len(deltas)} seeds  -> {verdict}"
        )


def render_multiseed(rows: list[dict]) -> None:
    """Stability of the shipped configuration, across streams."""
    full = [row for row in rows if row["variant"] == "full"]
    print()
    print("=" * 78)
    print("MULTI-SEED STABILITY (full configuration)")
    print("=" * 78)
    if len(full) < 2:
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
        ("headroom_pct", "headroom %", "{:.3f}"),
    ):
        lo, mid, hi = _spread([row[key] for row in full])
        print(
            f"{label:<20}{fmt.format(lo):>12}{fmt.format(mid):>12}{fmt.format(hi):>12}"
            f"{fmt.format(hi - lo):>12}"
        )

    print()
    print("per seed - the diagnostic that says WHERE the instability is:")
    print(f"{'seed':>10}{'threshold':>11}{'PR-AUC':>9}{'precision':>11}{'recall':>9}")
    for row in full:
        print(
            f"{row['seed']:>10}{row['threshold']:>11.2f}{row['pr_auc']:>9.4f}"
            f"{row['precision']:>11.4f}{row['recall']:>9.4f}"
        )
    pr_lo, _, pr_hi = _spread([r["pr_auc"] for r in full])
    th_lo, _, th_hi = _spread([r["threshold"] for r in full])
    print()
    print(f"PR-AUC is threshold-free, so its spread ({pr_hi - pr_lo:.4f}) is the detector")
    print(f"and the data, not the operating point. Thresholds ranged {th_lo:.2f} to {th_hi:.2f}.")


def render_verdict(result: dict, rows: list[dict], model: CostModel) -> None:
    """The honest summary, after the spread is known."""
    full = [row for row in rows if row["variant"] == "full"]
    print()
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    confusion = result["confusion"]
    if len(full) < 2:
        print("single seed. Run with SEEDS=3 before believing any of the above.")
        return

    p_lo, p_mid, p_hi = _spread([r["precision"] for r in full])
    r_lo, r_mid, r_hi = _spread([r["recall"] for r in full])
    n_lo, n_mid, n_hi = _spread([r["net_rupees"] for r in full])

    print(f"headline seed: precision {confusion.precision:.4f}, recall {confusion.recall:.4f}.")
    print(
        f"across {len(full)} streams: precision {p_mid:.3f} [{p_lo:.2f}, {p_hi:.2f}], "
        f"recall {r_mid:.3f} [{r_lo:.2f}, {r_hi:.2f}],"
    )
    print(f"net {R}{n_mid:,.0f} [{R}{n_lo:,.0f}, {R}{n_hi:,.0f}].")
    print()
    if (p_hi - p_lo) > 0.15 or (r_hi - r_lo) > 0.15:
        print("*** THE HEADLINE NUMBERS ARE NOT STABLE. ***")
        print("Report the median and the range. See docs/FAILURE_MODES.md 0.")
    else:
        print("the spread is narrow enough to report the median with its range attached.")
        print("quote the median, never the single-seed figure on its own.")


def summarise(result: dict, rows: list[dict]) -> dict:
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
        "seed_matrix": rows,
    }


if __name__ == "__main__":
    raise SystemExit(main())
