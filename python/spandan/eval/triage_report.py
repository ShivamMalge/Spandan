"""Run the triage graph over the test window and report what it changed.

The detector is frozen and its scores are untouched: this pass re-derives every
flag with the reference engine's streaming path (so the graph gets a real `Flag`
with its evidence), asserts each score is exactly the one the harness already
computed, and then lets the graph decide what the flag becomes. The report is
the difference between "flagged" and "declined" — which is the whole point of
a post-detection layer — plus the kill-switch trips that produced it.

This module imports `spandan.triage`, which imports nothing from `spandan.llm`;
the graph runs here with no explainer wired. That is asserted by the
poisoned-import test, which runs this pass with `spandan.llm` unimportable.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np

from ..detect import DetectorConfig, ReferenceDetector
from ..triage.graph import TriageConfig, TriageContext, run
from . import metrics
from .costs import CostModel, compute_costs
from .loader import Split

AUDIT_FILENAME = "audit.jsonl"


def run_triage(
    split: Split,
    test_scores: np.ndarray,
    threshold: float,
    model: CostModel,
    config: DetectorConfig,
    mode: str = "inline",
    audit_path: Path | str | None = None,
) -> dict:
    """Walk the test window through the graph. Returns the comparison, raw vs triaged."""
    cfg = TriageConfig.load(mode=mode)
    ctx = TriageContext(audit_path=audit_path)

    detector = ReferenceDetector(config.with_(threshold=threshold))
    for event in split.train_warmup:
        detector.update(event)
    for event in split.validation:
        detector.update(event)

    decisions: list[str] = []
    new_alerts = 0
    for i, event in enumerate(split.test):
        ctx.observe(event)
        flag = detector.update(event)
        if flag is None:
            decisions.append("")
            continue
        if flag.score != test_scores[i]:
            raise RuntimeError(
                f"streaming score {flag.score!r} != batch score {test_scores[i]!r} at {event.txn_id}; "
                "the triage pass must see exactly the scores the harness reported"
            )
        state = run(event, flag, ctx, cfg)
        decisions.append(state.decision)
        new_alerts += int(state.new_alert)
    ctx.close()

    declined = np.array([d == "decline" for d in decisions])
    labels = np.array([e.label for e in split.test])
    flagged = test_scores > threshold
    assert int(flagged.sum()) == sum(1 for d in decisions if d), "every flag must reach the graph"

    # Scores masked so that only DECLINED events count: the cost model and the
    # confusion matrix then price what the merchant actually experienced.
    declined_scores = np.where(declined, test_scores, -np.inf)
    confusion_raw = metrics.confusion_at(test_scores, labels, threshold)
    confusion_tri = metrics.confusion_at(declined_scores, labels, threshold)
    costs_raw = compute_costs(split.test, test_scores, threshold, model, new_alerts)
    costs_tri = compute_costs(split.test, declined_scores, threshold, model, new_alerts)
    clean = int((labels == 0).sum())

    per_scenario: dict[str, dict] = defaultdict(lambda: {"events": 0, "flagged": 0, "declined": 0, "alerted": 0})
    for e, d, f in zip(split.test, decisions, flagged):
        row = per_scenario[e.scenario_id]
        row["events"] += 1
        row["flagged"] += int(bool(f))
        row["declined"] += int(d == "decline")
        row["alerted"] += int(d.startswith("alert"))

    return {
        "mode": mode,
        "config": {
            "retry_ratio": cfg.kill_switch_retry_ratio,
            "min_events": cfg.kill_switch_min_events,
            "cooldown_ms": cfg.kill_switch_cooldown_ms,
        },
        "trips": ctx.trips,
        "flagged": int(flagged.sum()),
        "declined": int(declined.sum()),
        "alerts": new_alerts,
        "legit_decline_rate_raw": confusion_raw.fp / clean if clean else 0.0,
        "legit_decline_rate": confusion_tri.fp / clean if clean else 0.0,
        "fp_raw": confusion_raw.fp,
        "fp_declined": confusion_tri.fp,
        "tp_raw": confusion_raw.tp,
        "tp_declined": confusion_tri.tp,
        "blocked_good_rupees_raw": costs_raw.blocked_good_paise / 100.0,
        "blocked_good_rupees": costs_tri.blocked_good_paise / 100.0,
        "gross_rupees_raw": costs_raw.gross_paise / 100.0,
        "gross_rupees": costs_tri.gross_paise / 100.0,
        "per_scenario": dict(per_scenario),
        "audit_path": str(audit_path) if audit_path else None,
        "audit_entries": len(ctx.audit),
    }


def render_triage(report: dict) -> None:
    bar = "=" * 78
    print(bar)
    print(f"TRIAGE - what each flag became, through the graph (mode: {report['mode']})")
    print(bar)
    c = report["config"]
    print(
        "kill-switch: alert-only for one (merchant, BIN) when trailing-hour attempts per\n"
        f"distinct card >= {c['retry_ratio']:g} over >= {c['min_events']} events; holds "
        f"{c['cooldown_ms'] // 60000} min. Registered on TRAIN before this was measured (costs.toml)."
    )
    print()
    print(f"{'scenario':26} {'events':>8} {'flagged':>8} {'declined':>9} {'alerted':>8}")
    order = ("benign", "flash_sale", "issuer_outage", "outage_single_merchant", "burst", "rotating", "slow_low")
    for name in order:
        row = report["per_scenario"].get(name)
        if row:
            print(f"{name:26} {row['events']:>8} {row['flagged']:>8} {row['declined']:>9} {row['alerted']:>8}")
    print()
    print(f"kill-switch trips                    {len(report['trips'])}")
    if report["trips"]:
        keys = sorted({(t["merchant_id"], t["bin"]) for t in report["trips"]})
        print(f"  distinct (merchant, BIN) tripped   {len(keys)}")
    print(f"flagged -> declined                  {report['flagged']:,} -> {report['declined']:,}")
    print(f"alerts opened                        {report['alerts']:,}")
    print()
    raw, tri = report["legit_decline_rate_raw"], report["legit_decline_rate"]
    one_in = lambda r: f"1 in {round(1 / r):,}" if r > 0 else "none"  # noqa: E731
    print(f"legitimate transactions declined     raw {raw:.4f} ({one_in(raw)})   with triage {tri:.4f} ({one_in(tri)})")
    print(f"false positives that declined        raw {report['fp_raw']:,}   with triage {report['fp_declined']:,}")
    print(f"true positives that declined         raw {report['tp_raw']:,}   with triage {report['tp_declined']:,}")
    print(f"blocked-good cost                    raw Rs {report['blocked_good_rupees_raw']:,.0f}   with triage Rs {report['blocked_good_rupees']:,.0f}")
    print(f"gross (before review cost)           raw Rs {report['gross_rupees_raw']:,.0f}   with triage Rs {report['gross_rupees']:,.0f}")
    print()
    print("This is a ROUTING result, not a detector result: every score above is the")
    print("frozen detector's. The graph decided what each flag became, and wrote why")
    if report["audit_path"]:
        print(f"before acting - {report['audit_entries']:,} entries in {report['audit_path']}.")
    else:
        print(f"before acting - {report['audit_entries']:,} audit entries (not written to disk).")
    print()


def summarise_triage(report: dict) -> dict:
    return {k: v for k, v in report.items() if k != "trips"} | {"trips": len(report["trips"])}
