"""Metrics, including the two the plan treats as closer to the product than recall.

Ordinary metrics — precision, recall, F1, PR-AUC — plus:

- **Time to detection**, per scenario: how many events and how many rupees went
  through before the episode was first flagged. For a spike detector this is
  nearer to the product than recall is. Catching a burst at attempt 40 and
  catching it at attempt 400 are both `recall = 1` and are not the same outcome.
- **Alerts**, as distinct from flagged events. A human reviews an alert, not an
  event. 900 flagged events inside one episode is one thing to look at, not 900,
  and costing it as 900 would make the review cost meaningless.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..gen.schema import ATTACK_SCENARIOS, SCENARIOS, STATUS_APPROVED, Event

#: Two flagged events on the same (merchant, BIN) inside this gap are the same
#: alert. An analyst looking at a spike sees one thing, not one per transaction.
ALERT_COOLDOWN_MS = 15 * 60 * 1000


@dataclass(frozen=True, slots=True)
class Confusion:
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def confusion_at(scores: np.ndarray, labels: np.ndarray, threshold: float) -> Confusion:
    flagged = scores > threshold
    positive = labels == 1
    return Confusion(
        tp=int(np.sum(flagged & positive)),
        fp=int(np.sum(flagged & ~positive)),
        fn=int(np.sum(~flagged & positive)),
        tn=int(np.sum(~flagged & ~positive)),
    )


def average_precision(scores: np.ndarray, labels: np.ndarray) -> float:
    """Area under the precision-recall curve, by the step-wise sum.

    AP = sum over thresholds of (R_k - R_{k-1}) * P_k, which is the estimator
    that does not reward interpolation across a sparse curve. Verified in
    `tests/test_eval.py` against an independent brute-force implementation
    rather than against a library, so no new dependency is required.
    """
    if len(scores) == 0:
        return 0.0
    order = np.argsort(-scores, kind="stable")
    ordered = labels[order]
    total_positive = int(ordered.sum())
    if total_positive == 0:
        return 0.0

    tp = np.cumsum(ordered)
    fp = np.cumsum(1 - ordered)
    precision = tp / np.maximum(tp + fp, 1)
    recall = tp / total_positive

    previous_recall = 0.0
    area = 0.0
    for i in range(len(ordered)):
        if ordered[i] == 1:
            area += (recall[i] - previous_recall) * precision[i]
            previous_recall = recall[i]
    return float(area)


def per_scenario_counts(
    events: list[Event], scores: np.ndarray, threshold: float
) -> dict[str, dict]:
    """Flag rate per scenario. For attacks this is recall; for the clean
    scenarios it is a false-positive count, and they are not averaged together."""
    out: dict[str, dict] = {}
    for name in SCENARIOS:
        idx = [i for i, e in enumerate(events) if e.scenario_id == name]
        if not idx:
            continue
        flagged = int(np.sum(scores[idx] > threshold))
        out[name] = {
            "events": len(idx),
            "flagged": flagged,
            "rate": flagged / len(idx),
            "is_attack": name in ATTACK_SCENARIOS,
        }
    return out


def alerts(
    events: list[Event], scores: np.ndarray, threshold: float
) -> list[dict]:
    """Collapse flagged events into what a human would actually pick up.

    One alert per (merchant, BIN) run, closed after `ALERT_COOLDOWN_MS` of quiet.
    """
    open_alerts: dict[tuple[str, str], dict] = {}
    closed: list[dict] = []
    for i, event in enumerate(events):
        if scores[i] <= threshold:
            continue
        key = (event.merchant_id, event.bin)
        current = open_alerts.get(key)
        if current is not None and event.ts - current["last_ts"] <= ALERT_COOLDOWN_MS:
            current["events"] += 1
            current["last_ts"] = event.ts
            current["max_score"] = max(current["max_score"], float(scores[i]))
            current["labels"].append(event.label)
            continue
        if current is not None:
            closed.append(current)
        open_alerts[key] = {
            "merchant_id": event.merchant_id,
            "bin": event.bin,
            "first_ts": event.ts,
            "last_ts": event.ts,
            "events": 1,
            "max_score": float(scores[i]),
            "labels": [event.label],
        }
    closed.extend(open_alerts.values())
    for alert in closed:
        alert["is_true"] = sum(alert["labels"]) > 0
    return closed


def alerts_per_day(events: list[Event], alert_list: list[dict]) -> float:
    if not events:
        return 0.0
    span_ms = max(e.ts for e in events) - min(e.ts for e in events)
    days = max(span_ms / 86_400_000.0, 1e-9)
    return len(alert_list) / days


def time_to_detection(
    events: list[Event],
    scores: np.ndarray,
    threshold: float,
    episode_windows: list[dict],
) -> dict[str, dict]:
    """Per scenario: events elapsed and rupees exposed before the first flag.

    An episode never flagged contributes to `missed` and is excluded from the
    medians rather than being folded in as some arbitrary large number — an
    imputed value would quietly flatter or punish the median depending on what
    was chosen.
    """
    by_scenario: dict[str, dict] = {}
    for window in episode_windows:
        name = window["scenario_id"]
        if name not in ATTACK_SCENARIOS:
            continue
        idx = [
            i
            for i, e in enumerate(events)
            if e.scenario_id == name and window["start_ms"] <= e.ts <= window["end_ms"]
        ]
        if not idx:
            continue
        bucket = by_scenario.setdefault(
            name, {"episodes": 0, "missed": 0, "events_before": [], "rupees_before": []}
        )
        bucket["episodes"] += 1

        first = next((rank for rank, i in enumerate(idx) if scores[i] > threshold), None)
        if first is None:
            bucket["missed"] += 1
            continue
        bucket["events_before"].append(first)
        bucket["rupees_before"].append(
            sum(events[i].amount_paise for i in idx[:first]) / 100.0
        )

    for bucket in by_scenario.values():
        for key in ("events_before", "rupees_before"):
            values = bucket.pop(key)
            stem = key.replace("_before", "")
            if values:
                bucket[f"median_{stem}"] = float(np.median(values))
                bucket[f"p90_{stem}"] = float(np.percentile(values, 90))
            else:
                bucket[f"median_{stem}"] = math.inf
                bucket[f"p90_{stem}"] = math.inf
    return by_scenario


# --- vectorised sweep support ------------------------------------------------
#
# The threshold sweep evaluates 60 operating points. Doing that with the
# per-event Python loops above cost ~100s per sweep on a 100-day stream, which
# dominated `make eval`. These two functions compute the same quantities with
# numpy, and `tests/test_eval.py` asserts they agree with the loops rather than
# assuming it - a fast path that quietly disagrees with the reference would be
# worse than a slow one.


class SweepPrecompute:
    """Arrays derived once from the candidate events, reused for every threshold."""

    __slots__ = (
        "order", "scores", "labels", "amounts", "approved",
        "group", "ts", "n",
    )

    def __init__(self, events: list[Event], scores: np.ndarray):
        # Sorted by (merchant, bin, ts) so alert runs are contiguous.
        groups = {}
        codes = np.empty(len(events), dtype=np.int64)
        for i, event in enumerate(events):
            key = (event.merchant_id, event.bin)
            code = groups.get(key)
            if code is None:
                code = len(groups)
                groups[key] = code
            codes[i] = code

        ts = np.array([e.ts for e in events], dtype=np.int64)
        self.order = np.lexsort((ts, codes))
        self.group = codes[self.order]
        self.ts = ts[self.order]
        self.scores = scores[self.order]
        self.labels = np.array([e.label for e in events], dtype=np.int64)[self.order]
        self.amounts = np.array([e.amount_paise for e in events], dtype=np.float64)[self.order]
        self.approved = np.array(
            [e.status == STATUS_APPROVED for e in events], dtype=bool
        )[self.order]
        self.n = len(events)


def alert_count_at(pre: "SweepPrecompute", threshold: float) -> int:
    """Number of distinct alerts, vectorised.

    An alert starts at a flagged event whose previous flagged event is either in
    a different (merchant, BIN) group or more than the cooldown earlier.
    """
    mask = pre.scores > threshold
    if not mask.any():
        return 0
    group = pre.group[mask]
    ts = pre.ts[mask]
    starts = np.empty(len(ts), dtype=bool)
    starts[0] = True
    starts[1:] = (group[1:] != group[:-1]) | ((ts[1:] - ts[:-1]) > ALERT_COOLDOWN_MS)
    return int(starts.sum())
