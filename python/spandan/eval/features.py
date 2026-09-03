"""Read-only feature extraction from the frozen reference detector.

Phase C asks whether six hand-weighted terms are enough by fitting a model that
learns its own weights over the same terms. The terms are not recomputed here.
Every event is pushed through `ReferenceDetector._advance`, the per-event step
that `update` and `score_batch` both call, and the six values are read out of
the `terms` dict the detector already builds for its own score. The detector
score is recorded alongside them, and
`test_features_are_read_from_detector_evidence_not_recomputed` asserts it equals,
bit for bit, the score the harness computes for the same event.

Three columns come from the event itself, not the detector: log1p of the amount
in paise, whether it was declined, and the hour of day (UTC, from the
timestamp). Labels and scenario ids are never read in the extraction path.
`labels_of` is the one function here that reads a label; the baseline fitter and
the evaluation call it, feature extraction does not, and
`test_baseline_never_sees_labels_at_feature_time` proves that by handing the
extractor events whose label raises when touched.

Python reference only. `_advance` is the reference implementation's private
per-event step and the Rust core exposes no equivalent surface. This is
analysis, not the runtime path, and the freeze on `reference.py` is untouched:
this module calls it and reads what it returns.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..detect import DetectorConfig, ReferenceDetector
from ..gen.schema import STATUS_DECLINED, Event
from .loader import Split, load_split

#: The six terms, in the order `ReferenceDetector._score` builds them. Each is
#: already multiplied by its hand weight (`DetectorConfig.w_*`), and the
#: detector score is exactly their sum.
TERM_NAMES = ("velocity_bin", "decline_bin", "amount", "velocity_ip", "repetition", "merchant_span")

#: Event-level columns the learned models may use in addition to the terms.
EXTRA_NAMES = ("log_amount", "declined", "hour_of_day")

FEATURE_NAMES = TERM_NAMES + EXTRA_NAMES
FEATURES_FILENAME = "features.npz"
_HOUR_MS = 3_600_000


@dataclass(frozen=True)
class Features:
    """Per-window feature matrices, row-aligned with the split's event lists."""

    names: tuple[str, ...]
    warmup_x: np.ndarray
    warmup_score: np.ndarray
    validation_x: np.ndarray
    validation_score: np.ndarray
    test_x: np.ndarray
    test_score: np.ndarray


def extract(events: list[Event], detector: ReferenceDetector) -> tuple[np.ndarray, np.ndarray]:
    """Stream `events` through `detector` and read features off its evidence.

    Advances the detector's state exactly as scoring would, so calling this on
    warm-up, then validation, then test with one detector reproduces the
    harness's single-pass scores. Reads no label and no scenario id.
    """
    x = np.empty((len(events), len(FEATURE_NAMES)), dtype=np.float64)
    score = np.empty(len(events), dtype=np.float64)
    for i, event in enumerate(events):
        value, evidence = detector._advance(event)
        terms = evidence["terms"]
        for j, name in enumerate(TERM_NAMES):
            x[i, j] = terms[name]
        x[i, 6] = math.log1p(event.amount_paise)
        x[i, 7] = 1.0 if event.status == STATUS_DECLINED else 0.0
        x[i, 8] = (event.ts // _HOUR_MS) % 24
        score[i] = value
    return x, score


def extract_split(
    split: Split, config: DetectorConfig | None = None, include_test: bool = True
) -> Features:
    """One pass over warm-up, validation and test in stream order.

    `include_test=False` stops after validation without touching the test list
    at all, which is what threshold selection needs and what the poisoned-split
    test checks.
    """
    detector = ReferenceDetector(config or DetectorConfig())
    warmup_x, warmup_score = extract(split.train_warmup, detector)
    validation_x, validation_score = extract(split.validation, detector)
    if include_test:
        test_x, test_score = extract(split.test, detector)
    else:
        test_x = np.empty((0, len(FEATURE_NAMES)), dtype=np.float64)
        test_score = np.empty(0, dtype=np.float64)
    return Features(
        names=FEATURE_NAMES,
        warmup_x=warmup_x,
        warmup_score=warmup_score,
        validation_x=validation_x,
        validation_score=validation_score,
        test_x=test_x,
        test_score=test_score,
    )


def labels_of(events: list[Event]) -> np.ndarray:
    """The only label read in this module. Fitting and evaluation call it;
    feature extraction never does."""
    return np.array([e.label for e in events], dtype=np.int64)


def save_features(path: Path | str, feats: Features) -> None:
    np.savez_compressed(
        path,
        names=np.array(feats.names),
        warmup_x=feats.warmup_x,
        warmup_score=feats.warmup_score,
        validation_x=feats.validation_x,
        validation_score=feats.validation_score,
        test_x=feats.test_x,
        test_score=feats.test_score,
    )


def load_features(path: Path | str) -> Features:
    with np.load(path) as z:
        return Features(
            names=tuple(str(n) for n in z["names"]),
            warmup_x=z["warmup_x"],
            warmup_score=z["warmup_score"],
            validation_x=z["validation_x"],
            validation_score=z["validation_score"],
            test_x=z["test_x"],
            test_score=z["test_score"],
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract detector-evidence features for the learned baselines.")
    parser.add_argument("--data", default="data")
    parser.add_argument("--out", default=None, help=f"default: <data>/{FEATURES_FILENAME}")
    args = parser.parse_args(argv)

    data_dir = Path(args.data)
    out = Path(args.out) if args.out else data_dir / FEATURES_FILENAME
    split = load_split(data_dir)
    feats = extract_split(split)
    save_features(out, feats)

    # The self-check the test makes, printed so a reader of the log sees it too:
    # the score this pass recorded is the score the harness computes.
    from . import harness

    validation_scores, test_scores = harness.score_split_once(split, DetectorConfig())
    identical = bool(
        np.array_equal(validation_scores, feats.validation_score)
        and np.array_equal(test_scores, feats.test_score)
    )
    print(f"features      {out}")
    print(f"columns       {', '.join(FEATURE_NAMES)}")
    print(f"warm-up       {feats.warmup_x.shape[0]:,} events")
    print(f"validation    {feats.validation_x.shape[0]:,} events")
    print(f"test          {feats.test_x.shape[0]:,} events")
    print(f"detector scores identical to the harness pass: {identical}")
    return 0 if identical else 1


if __name__ == "__main__":
    raise SystemExit(main())
