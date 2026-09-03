"""Phase C tests: the learned baselines are a like-for-like comparison.

Three properties, each one the plan named. Features come from the detector, not
a reimplementation; thresholds come from validation, never test; and feature
extraction never reads a label. Plus the control: the detector scored through
the baseline pipeline reproduces the harness field for field.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from helpers import SMALL_CONFIG  # noqa: E402
from spandan.detect import DetectorConfig
from spandan.eval import baselines, features
from spandan.eval.costs import CostModel
from spandan.eval.harness import evaluate, evaluate_scored, score_split_once
from spandan.eval.loader import load_split
from spandan.gen.build import build
from spandan.gen.schema import Event


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("baselinestream")
    manifest = build(SMALL_CONFIG, out)
    return {"dir": out, "manifest": manifest, "split": load_split(out)}


@pytest.fixture(scope="module")
def feats(built):
    return features.extract_split(built["split"])


def test_features_are_read_from_detector_evidence_not_recomputed(built, feats):
    """The detector score the extractor records is the harness's score, exactly,
    and the six term columns sum to it. Nothing was recomputed."""
    validation_scores, test_scores = score_split_once(built["split"], DetectorConfig())
    assert np.array_equal(feats.validation_score, validation_scores)
    assert np.array_equal(feats.test_score, test_scores)
    assert feats.names == features.FEATURE_NAMES
    term_sum = feats.test_x[:, : len(features.TERM_NAMES)].sum(axis=1)
    assert np.allclose(term_sum, feats.test_score, atol=1e-9, rtol=0.0)
    assert feats.test_x.shape == (len(built["split"].test), len(features.FEATURE_NAMES))


def test_baseline_threshold_selected_on_validation_only(built):
    """Poison the test split so any read during fitting or selection crashes,
    the harness's own pattern."""

    class Poisoned(list):
        def __iter__(self):
            raise AssertionError("baseline fitting or selection read the TEST window")

        def __getitem__(self, item):
            raise AssertionError("baseline fitting or selection read the TEST window")

    poisoned = dataclasses.replace(built["split"], test=Poisoned(built["split"].test))
    blind = features.extract_split(poisoned, include_test=False)
    assert blind.test_x.shape[0] == 0
    model = CostModel.load()
    for name in baselines.MODELS:
        scorer, chosen = baselines.select_operating_point(name, poisoned, blind, model, model.alerts_per_day_budget)
        assert math.isfinite(chosen["threshold"])
        assert scorer.name == name


def test_baseline_never_sees_labels_at_feature_time(built):
    """Events whose label and scenario id raise on read go through extraction
    untouched. The fitter, which must read labels, is shown to raise on them."""

    def _raise(_self):
        raise AssertionError("feature extraction read a LABEL or scenario id")

    class Blind(Event):
        __slots__ = ()
        label = property(_raise, lambda _self, _value: None)
        scenario_id = property(_raise, lambda _self, _value: None)

    fields = [f.name for f in dataclasses.fields(Event)]
    source = built["split"].validation[:500]
    blind = [Blind(**{f: getattr(e, f) for f in fields}) for e in source]
    with pytest.raises(AssertionError, match="LABEL"):
        _ = blind[0].label

    from spandan.detect import ReferenceDetector

    x, score = features.extract(blind, ReferenceDetector(DetectorConfig()))
    assert x.shape == (500, len(features.FEATURE_NAMES))
    assert np.all(np.isfinite(x)) and np.all(np.isfinite(score))
    with pytest.raises(AssertionError, match="LABEL"):
        features.labels_of(blind)


def test_hand_row_reproduces_the_harness(built, feats):
    """The control: the detector's own scores through `evaluate_scored` equal
    `evaluate`, so the learned rows are measured by the same yardstick."""
    split, model = built["split"], CostModel.load()
    windows = built["manifest"]["episode_windows"]
    direct = evaluate(split, DetectorConfig(), model, windows)
    scored = evaluate_scored(split, feats.validation_score, feats.test_score, model, windows)
    assert scored["threshold"] == direct["threshold"]
    assert scored["confusion"] == direct["confusion"]
    assert scored["costs"] == direct["costs"]
    assert scored["reweighted"] == direct["reweighted"]
    assert scored["alerts_per_day"] == direct["alerts_per_day"]

    results = baselines.run_models(split, feats, model, windows, names=("hand",))
    assert results["hand"]["precision"] == direct["confusion"].precision
    assert results["hand"]["threshold"] == direct["threshold"]


def test_learned_models_run_end_to_end(built, feats):
    """Both learned models fit, select under the budget, and report finite
    figures through the same summary as the detector."""
    split, model = built["split"], CostModel.load()
    windows = built["manifest"]["episode_windows"]
    results = baselines.run_models(split, feats, model, windows)
    assert set(results) == set(baselines.MODELS)
    for name, row in results.items():
        assert 0.0 <= row["precision"] <= 1.0 and 0.0 <= row["recall"] <= 1.0, name
        assert math.isfinite(row["net_rupees"]), name
        # The budget binds where the threshold is chosen, on validation. Test
        # alerts/day is reported, not capped - the harness makes the same choice.
        _, chosen = baselines.select_operating_point(name, split, feats, model, model.alerts_per_day_budget)
        assert chosen["alerts_per_day"] <= model.alerts_per_day_budget or chosen.get("budget_infeasible"), name
    weights = results["logreg6"]["weights"]
    assert set(weights) == set(features.TERM_NAMES)
    assert results["gbm9"]["weights"] is None


def test_detector_package_does_not_import_the_baselines_or_sklearn():
    """The learned models are reported, not shipped: nothing under
    `spandan.detect` may reach scikit-learn or the baselines module."""
    import subprocess
    import sys

    code = (
        "import sys; import spandan.detect; "
        "bad = [m for m in sys.modules if m.startswith('sklearn') or m.startswith('spandan.eval')]; "
        "print(bad)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", out.stdout
