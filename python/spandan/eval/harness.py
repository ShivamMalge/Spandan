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
from ..detect.rust_engine import ENGINES, make_detector
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


#: Engine used by every scoring call this run. Set once in main() from
#: --engine / ENGINE=, read by the scoring helpers, and printed in the report
#: header so the provenance of every number includes which core produced it.
ACTIVE_ENGINE = "python"


def score_stream(events: list[Event], config: DetectorConfig) -> np.ndarray:
    return make_detector(ACTIVE_ENGINE, config).score_batch(events)


def score_with_warmup(
    warmup: list[Event], target: list[Event], config: DetectorConfig
) -> np.ndarray:
    """Warm the detector on earlier events, then score the target window.

    Without this, threshold selection would be measuring cold-start behaviour:
    every entity's baseline would be empty for the first stretch of the window
    and the scores would be an artifact of that rather than of the traffic.
    """
    detector = make_detector(ACTIVE_ENGINE, config)
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
    detector = make_detector(ACTIVE_ENGINE, config)
    detector.score_batch(split.train_warmup)
    validation_scores = detector.score_batch(split.validation)
    test_scores = detector.score_batch(split.test)
    return validation_scores, test_scores


# --- threshold selection, on validation only --------------------------------


def sweep_thresholds(
    events: list[Event],
    scores: np.ndarray,
    model: CostModel,
    count: int = 600,
) -> list[dict]:
    """Net rupee position across the full threshold range.

    600 points, not 60. Alerts/day is extremely steep in the threshold near the
    operating region - at 60 points the whole band between 6 and 28 alerts/day was
    unsampled, so the frontier could not distinguish budgets of 10 and 20 and the
    most interesting part of the trade-off was invisible. The sweep is vectorised
    and costs ~0.4s per 60 points, so resolution here is nearly free.

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

    span_days = max((max(e.ts for e in events) - min(e.ts for e in events)) / 86_400_000.0, 1e-9)

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
                "alerts_per_day": alerts / span_days,
                "precision": confusion.precision,
                "recall": confusion.recall,
                "break_even_paise": (gross / alerts) if alerts else 0.0,
            }
        )
    return rows


#: Two operating points whose net positions are within this of each other are
#: treated as economically equivalent, and the tie is broken toward recall.
NET_EQUIVALENCE_BAND = 0.05


def select_threshold(rows: list[dict], alerts_per_day_budget: float | None = None) -> dict:
    """Maximum net rupees **subject to an alert budget**.

    Why constrained rather than plain argmax, which is what Phase 2 did:

    The unconstrained criterion selects the operating point that maximises net
    rupees under the cost model. But the Phase 2 addendum demonstrated that the
    cost model prices 10,759 wrongly-blocked legitimate transactions at Rs 13,121,
    because that traffic carries low-value baskets and was mostly declining
    anyway. A model that cheap about false positives will happily buy recall with
    them - and it did, landing on threshold 21.15, 27.6 alerts/day, and precision
    0.069 at a realistic base rate.

    So the operating point was being chosen by an objective already shown to be
    wrong about the thing it was trading away. The constraint is the fix that does
    not require trusting the rupee model to price false positives correctly: cap
    the alert queue at what a human can actually work through, then maximise net
    inside that cap.

    The cap is an assumption (`costs.toml [operations]`), and the harness reports
    the whole frontier so the assumption can be argued with.

    Ties within `NET_EQUIVALENCE_BAND` still break toward recall: if two points
    make the merchant the same money and both fit the budget, the one that catches
    more attacks is the better product.
    """
    feasible = rows
    if alerts_per_day_budget is not None:
        feasible = [row for row in rows if row["alerts_per_day"] <= alerts_per_day_budget]
        if not feasible:
            # No operating point fits the budget. Take the quietest one available
            # and let the report say so, rather than silently ignoring the cap.
            return min(rows, key=lambda row: row["alerts_per_day"]) | {"budget_infeasible": True}

    best = max(row["net_paise"] for row in feasible)
    if best <= 0:
        return max(feasible, key=lambda row: row["net_paise"])
    floor = best * (1.0 - NET_EQUIVALENCE_BAND)
    equivalent = [row for row in feasible if row["net_paise"] >= floor]
    return min(equivalent, key=lambda row: row["threshold"])


# --- the report --------------------------------------------------------------


def evaluate(
    split: Split,
    config: DetectorConfig,
    model: CostModel,
    episode_windows: list[dict],
    alerts_per_day_budget: float | None = None,
) -> dict:
    validation_scores, test_scores = score_split_once(split, config)
    sweep = sweep_thresholds(split.validation, validation_scores, model)
    budget = model.alerts_per_day_budget if alerts_per_day_budget is None else alerts_per_day_budget
    chosen = select_threshold(sweep, budget)
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

    true_alerts = sum(1 for a in alert_list if a["is_true"])
    clean_events = int(np.sum(labels == 0))
    return {
        "legit_decline_rate": confusion.fp / clean_events if clean_events else 0.0,
        "clean_events": clean_events,
        "flag_rate": float(np.mean(test_scores > threshold)),
        "alert_precision": true_alerts / len(alert_list) if alert_list else 0.0,
        "true_alerts": true_alerts,
        "margin": margin,
        "threshold": threshold,
        "threshold_source": {
            "window": "validation",
            "start_ms": split.validation_start_ms,
            "end_ms": max(e.ts for e in split.validation),
            "events": len(split.validation),
            "criterion": (
                f"max net rupees subject to alerts/day <= {budget:g}"
                if budget is not None
                else "max net rupees, unconstrained"
            ),
            "alerts_per_day_budget": budget,
            "budget_infeasible": chosen.get("budget_infeasible", False),
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


def run_budget_frontier(
    split: Split,
    config: DetectorConfig,
    model: CostModel,
    episode_windows: list[dict],
) -> list[dict]:
    """The operating-point frontier: what each alert budget buys.

    One threshold selection per budget, each made on the validation window, then
    evaluated on test. Scoring happens once and is reused across budgets, so this
    costs almost nothing beyond the sweep.

    **This is a sensitivity table, not a menu.** The headline operating point is
    the budget stated in `costs.toml`, fixed before test was read. Reporting the
    frontier lets a reader disagree with that assumption; picking the flattering
    row after seeing these numbers would be selecting on the test set, which is
    the thing this project exists not to do.
    """
    validation_scores, test_scores = score_split_once(split, config)
    sweep = sweep_thresholds(split.validation, validation_scores, model)
    labels = np.array([e.label for e in split.test])

    rows = []
    for budget in model.frontier_budgets:
        chosen = select_threshold(sweep, budget)
        threshold = chosen["threshold"]
        confusion = metrics.confusion_at(test_scores, labels, threshold)
        alert_list = metrics.alerts(split.test, test_scores, threshold)
        breakdown = compute_costs(split.test, test_scores, threshold, model, len(alert_list))
        reweighted = reweight_to_prevalence(
            confusion.tp, confusion.fp, confusion.fn, confusion.tn, model.target_prevalence
        )
        ttd = metrics.time_to_detection(split.test, test_scores, threshold, episode_windows)
        episodes = sum(row["episodes"] for row in ttd.values())
        caught = sum(row["episodes"] - row["missed"] for row in ttd.values())
        medians = [
            row["median_events"] for row in ttd.values() if not math.isinf(row["median_events"])
        ]
        flagged_mask = test_scores > threshold
        clean_events = int(np.sum(labels == 0))
        rows.append(
            {
                "budget": budget,
                "legit_decline_rate": confusion.fp / clean_events if clean_events else 0.0,
                "threshold": threshold,
                "flag_rate": float(flagged_mask.mean()),
                "flagged_events": int(flagged_mask.sum()),
                "events_per_alert": (
                    int(flagged_mask.sum()) / len(alert_list) if alert_list else 0.0
                ),
                "validation_net_rupees": chosen["net_paise"] / 100.0,
                "validation_alerts_per_day": chosen["alerts_per_day"],
                "alerts_per_day": metrics.alerts_per_day(split.test, alert_list),
                "precision": confusion.precision,
                "precision_at_target": reweighted.precision_target,
                "recall": confusion.recall,
                "episodes_caught": caught,
                "episodes": episodes,
                "median_ttd_events": float(np.median(medians)) if medians else math.inf,
                "net_rupees": breakdown.net_paise(model.assumed_review_paise) / 100.0,
                "infeasible": chosen.get("budget_infeasible", False),
            }
        )
    return rows


def render_frontier(rows: list[dict], model: CostModel) -> None:
    print()
    print("=" * 78)
    print("OPERATING-POINT FRONTIER -- what each alert budget buys")
    print("=" * 78)
    print("The threshold is chosen to maximise net rupees SUBJECT TO an alerts/day cap,")
    print("not to maximise net rupees outright. The unconstrained criterion was selecting")
    print("the operating point using a cost model that prices a wrongly-blocked declining")
    print("transaction at almost nothing - so it bought recall with false positives.")
    print()
    print(f"{'budget':>8}{'thresh':>9}{'alerts/d':>10}{'ev/alert':>10}{'flag rate':>11}"
          f"{'declined':>10}{'prec':>8}{'prec@0.15%':>12}{'recall':>8}{'episodes':>10}"
          f"{'med TTD':>9}{'val net':>12}{'test net':>12}")
    for row in rows:
        marker = " <== HEADLINE" if row["budget"] == model.alerts_per_day_budget else ""
        ttd = row["median_ttd_events"]
        caught_cell = f"{row['episodes_caught']}/{row['episodes']}"
        print(
            f"{row['budget']:>8.0f}{row['threshold']:>9.2f}{row['alerts_per_day']:>10.1f}"
            f"{row['events_per_alert']:>10.0f}{row['flag_rate']:>11.4f}"
            f"{row['legit_decline_rate']:>10.4f}"
            f"{row['precision']:>8.3f}{row['precision_at_target']:>12.4f}"
            f"{row['recall']:>8.3f}"
            f"{caught_cell:>10}"
            f"{('never' if math.isinf(ttd) else f'{ttd:.0f}'):>9}"
            f"{R + format(row['validation_net_rupees'], ',.0f'):>12}"
            f"{R + format(row['net_rupees'], ',.0f'):>12}{marker}"
        )
    print()
    print("`prec@0.15%` is precision reweighted to a realistic merchant card-testing")
    print("base rate. It is the number an operations team would actually live with.")
    print()
    print("`declined` is the share of LEGITIMATE transactions the control declines -")
    print("false positives over clean events. A flag declines, so this is what a merchant")
    print("feels. It is the deployability number, and no alert budget bounds it.")
    print()
    print("AN ALERT BUDGET DOES NOT CONSTRAIN EVENT-LEVEL OVER-TRIGGERING.")
    print("`ev/alert` is how many flagged events each alert collapses. Alerts are")
    print("deduplicated per (merchant, BIN) with a 15-minute cooldown, so a threshold low")
    print("enough to flag 2.5% of ALL traffic still produces only ~10 alerts a day - the")
    print("flood hides inside the dedup. That is why `med TTD` returns to 0 at the looser")
    print("budgets: episodes are caught on their first event because almost everything is")
    print("being flagged. Read `flag rate` and `ev/alert` alongside the budget; the budget")
    print("alone is a weak proxy for how noisy the detector actually is.")
    print()
    print("WHY `net` IS NOT MONOTONE IN THE BUDGET. A larger budget permits every")
    print("threshold a smaller one permits, so the constrained maximum cannot fall -")
    print("and on the VALIDATION window, where selection happens, it does not: net rises")
    print("monotonically with the budget. `net` here is measured on TEST, at a threshold")
    print("chosen on validation, so the two need not agree. Where the test column dips")
    print("as the budget loosens, that is the validation-to-test generalisation gap,")
    print("not a selection bug. It is worth reading as evidence in its own right: the")
    print("operating point that looked best on validation did not transfer best.")
    print()
    print("This table is a SENSITIVITY analysis. The headline budget was fixed in")
    print("costs.toml before the test window was read; choosing a row from here after")
    print("seeing these numbers would be selecting on the test set.")


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
    if source.get("budget_infeasible"):
        print("WARNING: no operating point met the alert budget; the quietest was taken.")
    print(f"threshold: {threshold:.4f}")
    print("the test window was not read until this value was fixed.")

    reweighted_lead = result["reweighted"]
    print()
    print("=" * 78)
    print("HEADLINE  -- precision at a realistic base rate")
    print("=" * 78)
    print(
        f"precision at the assumed {reweighted_lead.target_prevalence:.2%} merchant "
        f"card-testing rate:   {reweighted_lead.precision_target:.4f}"
    )
    if reweighted_lead.precision_target > 0:
        per_catch = (1 - reweighted_lead.precision_target) / reweighted_lead.precision_target
        print(f"that is roughly {per_catch:.0f} false alarms for every true catch.")
    print()
    print("This is the number a payments panel will look for, so it is the number this")
    print("report opens with.")
    print()
    print(f"Measured at the generator's own ~1.3% positive rate, precision is "
          f"{reweighted_lead.precision_observed:.4f}.")
    print("That figure flatters the detector by roughly an order of magnitude: real")
    print("merchant card-testing rates are far below the rate this stream was built at.")
    print("Recall is unaffected by base rate and is reported below unadjusted.")
    print()
    print(f"prevalence assumed: {reweighted_lead.target_prevalence:.2%}  (ASSUMPTION, costs.toml)")
    print(f"negatives rescaled x{reweighted_lead.negative_scale:,.1f} -> effective FP "
          f"{reweighted_lead.effective_fp:,.0f}")

    print()
    print("-- what a flag does " + "-" * 58)
    print("A flag DECLINES the transaction. Alerts are the human-facing grouping of")
    print("those declines per (merchant, BIN) - they are not a separate, softer action.")
    print(
        f"legitimate transactions declined: {result['legit_decline_rate']:.4%}  "
        f"({confusion.fp:,} of {result['clean_events']:,} clean events)"
    )
    if result["legit_decline_rate"] > 0:
        one_in = 1.0 / result["legit_decline_rate"]
        print(f"that is 1 in {one_in:,.0f} legitimate customers declined.")
    print(f"overall flag rate: {result['flag_rate']:.4%} of all traffic")
    print()
    print("An alerts/day budget bounds the ANALYST QUEUE and nothing else. It does not")
    print("bound merchant impact: the decline rate above is the number a merchant feels,")
    print("and it is the one that decides deployability.")

    print()
    print("=" * 78)
    print("DETECTION SPEED")
    print("=" * 78)
    print("A spike detector is judged on how fast it catches an episode, not only on")
    print("whether it eventually does. Attempt 40 and attempt 400 are both recall=1 and")
    print("are not the same outcome for the merchant. An episode has to be caught ONCE")
    print("to be acted on, so episode detection and event recall measure different")
    print("things and both are reported.")
    print()
    print("Read this against the alert budget above, not on its own. A median of 0 events")
    print("means episodes are caught on their first event - which a low enough threshold")
    print("will always achieve, by flagging almost everything. Fast detection bought by")
    print("over-triggering is not a result; these figures are measured at the CONSTRAINED")
    print("operating point so that they cost something.")
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
        print("NEGATIVE HEADROOM -- this is a result, and it is the sharpest one here.")
        print(f"The highest-scoring clean event ({margin['highest_clean_scenario']}) scores")
        print("ABOVE the threshold, so the two populations are not separated by the")
        print("detector at all - only by where the line happens to fall.")
        print()
        print("What that identifies: the detector has a blind spot on legitimate traffic")
        print("whose declines are concentrated on a single BIN at a single merchant. It")
        print("cannot tell that from card testing, because the feature that would - the")
        print("same card retried - is invisible at a 5-minute window. The control was")
        print("built to find exactly this, and it found it. See docs/FAILURE_MODES.md 2.")
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
    print(
        f"{'of those, genuinely card testing':<34}"
        f"{result['true_alerts']:>16}   ({result['alert_precision']:.1%})"
    )
    print("  alert-level precision is what an analyst actually experiences: of the")
    print("  things they open, how many are real. It is not the same as event-level")
    print("  precision and is the more operationally honest of the two.")
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
    parser.add_argument(
        "--engine",
        choices=ENGINES,
        default="python",
        help="which detector core scores the stream; the numbers must not depend on it",
    )
    parser.add_argument(
        "--triage-mode",
        choices=("inline", "alert_only", "off"),
        default="inline",
        help="run the post-detection graph over the test window; scores are unchanged either way",
    )
    args = parser.parse_args(argv)

    global ACTIVE_ENGINE
    ACTIVE_ENGINE = args.engine

    data_dir = Path(args.data)
    manifest = json.loads((data_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    episode_windows = manifest["episode_windows"]

    model = CostModel.load()
    config = DetectorConfig()
    split = load_split(data_dir)

    print(f"engine        {ACTIVE_ENGINE}")
    print(f"data          {data_dir.resolve()}")
    print(f"seed          {manifest['seed']}")
    print(f"config hash   {manifest['config_hash'][:16]}")
    print(split.describe())
    print()

    result = evaluate(split, config, model, episode_windows)
    render(result, model, split)
    render_frontier(run_budget_frontier(split, config, model, episode_windows), model)
    render_sweep(result, model)

    triage = None
    if args.triage_mode != "off":
        from .triage_report import AUDIT_FILENAME, render_triage, run_triage

        triage = run_triage(
            split, result["test_scores"], result["threshold"], model, config,
            mode=args.triage_mode, audit_path=data_dir / AUDIT_FILENAME,
        )
        render_triage(triage)

    rows = run_seed_matrix(args.seeds, manifest["seed"], config, model)
    render_ablation_matrix(rows, args.seeds)
    render_multiseed(rows)
    render_verdict(result, rows, model)

    if args.json_out:
        summary = summarise(result, rows)
        if triage is not None:
            from .triage_report import summarise_triage

            summary["triage"] = summarise_triage(triage)
        Path(args.json_out).write_text(
            json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
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
        "legit_decline_rate": result["legit_decline_rate"],
        "flag_rate": result["flag_rate"],
        "target_prevalence": reweighted.target_prevalence,
        "gross_rupees": costs.gross_paise / 100.0,
        "break_even_review_rupees": costs.break_even_review_paise() / 100.0,
        "alerts": costs.alerts,
        "alerts_per_day": result["alerts_per_day"],
        "per_scenario": result["per_scenario"],
        "engine": ACTIVE_ENGINE,
        "seed_matrix": rows,
    }


if __name__ == "__main__":
    raise SystemExit(main())
