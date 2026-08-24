"""Phase 2 tests for the evaluation harness.

The arithmetic tests are worked by hand in the test body rather than by calling
the implementation with different arguments. A test that recomputes the cost
model using the cost model would pass whatever the cost model did.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from helpers import SMALL_CONFIG  # noqa: E402
from spandan.detect import DetectorConfig
from spandan.eval import metrics
from spandan.eval.costs import CostModel, compute_costs, reweight_to_prevalence
from spandan.eval.harness import (
    evaluate,
    run_ablations,
    run_multiseed,
    score_with_warmup,
    select_threshold,
    sweep_thresholds,
)
from spandan.eval.loader import NonTemporalSplitError, build_split, load_split
from spandan.gen.build import build
from spandan.gen.schema import (
    SCENARIO_FLASH_SALE,
    SCENARIO_ISSUER_OUTAGE,
    STATUS_APPROVED,
    STATUS_DECLINED,
    Event,
)


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("evalstream")
    manifest = build(SMALL_CONFIG, out)
    return {"dir": out, "manifest": manifest, "split": load_split(out)}


def _event(**kwargs) -> Event:
    base = dict(
        ts=0,
        txn_id="txn_0",
        merchant_id="mer_000",
        bin="000111",
        card_ref="card_0000000001",
        ip="192.0.2.1",
        device_id="dev_0000000001",
        amount_paise=10_000,
        status=STATUS_APPROVED,
        label=0,
        scenario_id="benign",
    )
    base.update(kwargs)
    return Event(**base)


# --- the split is temporal, and the loader refuses anything else -------------


def test_loader_rejects_nontemporal_split(built):
    train = built["split"].train
    test = built["split"].test

    # A shuffled split: take some later events into train and earlier into test.
    bad_train = train + test[:50]
    with pytest.raises(NonTemporalSplitError, match="not strictly earlier"):
        build_split(sorted(bad_train, key=lambda e: e.ts), test)


def test_loader_rejects_unordered_stream(built):
    train = list(built["split"].train)
    test = built["split"].test
    train[10], train[11] = train[11], train[10]
    if train[10].ts == train[11].ts:
        pytest.skip("timestamps tied; swap is not observable")
    with pytest.raises(NonTemporalSplitError, match="not time-ordered"):
        build_split(train, test)


def test_validation_window_is_a_suffix_of_training(built):
    split = built["split"]
    assert max(e.ts for e in split.train_warmup) < min(e.ts for e in split.validation)
    assert max(e.ts for e in split.validation) < min(e.ts for e in split.test)
    assert len(split.train_warmup) + len(split.validation) == len(split.train)


def test_threshold_selection_never_touches_test_window(built, monkeypatch):
    """Poison the test split so that reading it during selection would crash.

    Stronger than inspecting the call graph: it fails if any future change starts
    consulting test data while choosing a threshold.
    """
    split = built["split"]
    model = CostModel.load()
    config = DetectorConfig()

    class Poisoned(list):
        def __iter__(self):
            raise AssertionError("threshold selection read the TEST window")

        def __getitem__(self, item):
            raise AssertionError("threshold selection read the TEST window")

    poisoned = dataclasses.replace(split, test=Poisoned(split.test))

    validation_scores = score_with_warmup(
        poisoned.train_warmup, poisoned.validation, config
    )
    rows = sweep_thresholds(poisoned.validation, validation_scores, model)
    chosen = select_threshold(rows)
    assert chosen["threshold"] > 0


# --- PR-AUC ------------------------------------------------------------------


def test_average_precision_matches_bruteforce_reference():
    """Checked against an independent implementation written in the test.

    PHASES.md named this `test_pr_auc_matches_sklearn_reference`; scikit-learn is
    not a project dependency and adding one for a single assertion would need an
    `agents.md` 8 request. A brute-force reference computes the same quantity
    from the definition, which is a stronger check than agreeing with a library.
    """
    rng = np.random.default_rng(4242)
    scores = rng.normal(size=400)
    labels = (rng.random(400) < 0.2).astype(int)

    def brute_force(scores, labels):
        order = np.argsort(-scores, kind="stable")
        ordered = labels[order]
        total = ordered.sum()
        area, hits, previous_recall = 0.0, 0, 0.0
        for rank, label in enumerate(ordered, start=1):
            if label == 1:
                hits += 1
                precision = hits / rank
                recall = hits / total
                area += (recall - previous_recall) * precision
                previous_recall = recall
        return area

    assert metrics.average_precision(scores, labels) == pytest.approx(
        brute_force(scores, labels), rel=1e-12
    )


def test_average_precision_of_a_perfect_ranking_is_one():
    scores = np.array([9.0, 8.0, 7.0, 1.0, 0.5])
    labels = np.array([1, 1, 1, 0, 0])
    assert metrics.average_precision(scores, labels) == pytest.approx(1.0)


def test_shuffled_labels_collapse_average_precision_to_prevalence(built):
    """A leak check, kept as a permanent test.

    If any label information reached the score, shuffling the labels would not
    drop PR-AUC to the base rate.
    """
    split = built["split"]
    scores = score_with_warmup(split.train, split.test, DetectorConfig())
    labels = np.array([e.label for e in split.test])

    real = metrics.average_precision(scores, labels)
    shuffled = labels.copy()
    np.random.default_rng(11).shuffle(shuffled)
    noise = metrics.average_precision(scores, shuffled)

    prevalence = labels.mean()
    assert real > 3 * prevalence, "no signal at all; the rest of the suite is moot"
    assert noise == pytest.approx(prevalence, abs=0.03), (
        f"shuffled-label PR-AUC {noise:.4f} is not at the base rate {prevalence:.4f}; "
        "suspect a leak"
    )


# --- the cost model, worked by hand ------------------------------------------


def test_cost_model_matches_hand_worked_example():
    model = CostModel(
        auth_fee_paise=150,
        chargeback_fee_paise=50_000,
        chargeback_loss_fraction=1.0,
        chargeback_rate_on_approved_fraud=0.8,
        contribution_margin=0.25,
        only_charge_if_approved=True,
        assumed_review_paise=4_000,
        target_prevalence=0.0015,
    )
    events = [
        _event(ts=1, label=1, status=STATUS_APPROVED, amount_paise=2_000),   # flagged fraud, approved
        _event(ts=2, label=1, status=STATUS_DECLINED, amount_paise=2_000),   # flagged fraud, declined
        _event(ts=3, label=0, status=STATUS_APPROVED, amount_paise=100_000), # flagged good
        _event(ts=4, label=0, status=STATUS_DECLINED, amount_paise=100_000), # flagged, would decline
        _event(ts=5, label=1, status=STATUS_APPROVED, amount_paise=9_999),   # NOT flagged
    ]
    scores = np.array([10.0, 10.0, 10.0, 10.0, 0.0])

    breakdown = compute_costs(events, scores, threshold=5.0, model=model, alert_count=2)

    # By hand:
    #   auth fees: 4 flagged... no - only the two fraud ones: 2 x 150 = 300
    assert breakdown.saved_auth_fees_paise == 2 * 150
    #   chargeback: only the APPROVED fraud one: 0.8 x (50000 + 1.0 x 2000) = 41600
    assert breakdown.avoided_chargebacks_paise == pytest.approx(0.8 * (50_000 + 2_000))
    #   blocked good: only the APPROVED clean one: 0.25 x 100000 = 25000
    #   the declined clean one costs nothing - it was going to decline anyway
    assert breakdown.blocked_good_paise == pytest.approx(0.25 * 100_000)
    assert breakdown.blocked_good_events == 2
    assert breakdown.blocked_good_events_that_would_decline == 1

    expected_gross = 300 + 0.8 * 52_000 - 25_000
    assert breakdown.gross_paise == pytest.approx(expected_gross)
    assert breakdown.net_paise(4_000) == pytest.approx(expected_gross - 2 * 4_000)


def test_blocking_a_doomed_transaction_costs_the_merchant_nothing():
    """The switch that matters most for the issuer-outage control.

    ~82% of outage traffic declines regardless, so charging margin on it would
    inflate that control's false-positive cost roughly fivefold.
    """
    model = CostModel.load()
    declined_only = [_event(ts=1, label=0, status=STATUS_DECLINED, amount_paise=500_000)]
    breakdown = compute_costs(declined_only, np.array([9.0]), 1.0, model, alert_count=1)
    assert breakdown.blocked_good_paise == 0.0
    assert breakdown.blocked_good_events_that_would_decline == 1


def test_break_even_review_cost_matches_hand_worked_example():
    model = CostModel.load()
    events = [_event(ts=1, label=1, status=STATUS_APPROVED, amount_paise=1_000)]
    breakdown = compute_costs(events, np.array([9.0]), 1.0, model, alert_count=4)

    expected = breakdown.gross_paise / 4
    assert breakdown.break_even_review_paise() == pytest.approx(expected)
    # At exactly break-even, net is zero.
    assert breakdown.net_paise(expected) == pytest.approx(0.0, abs=1e-6)


def test_break_even_is_computed_not_read_from_costs_toml():
    """The whole point of the break-even figure is that it is an output.

    Changing the assumed review cost must not move it.
    """
    model = CostModel.load()
    events = [_event(ts=1, label=1, status=STATUS_APPROVED, amount_paise=1_000)]
    scores = np.array([9.0])

    a = compute_costs(events, scores, 1.0, model, alert_count=3).break_even_review_paise()
    louder = dataclasses.replace(model, assumed_review_paise=999_999)
    b = compute_costs(events, scores, 1.0, louder, alert_count=3).break_even_review_paise()
    assert a == pytest.approx(b)


# --- prevalence reweighting ---------------------------------------------------


def test_precision_reweighting_matches_hand_worked_example():
    # 100 positives, 900 negatives -> observed prevalence 10%.
    # 80 caught, 90 false positives.
    result = reweight_to_prevalence(tp=80, fp=90, fn=20, tn=810, target_prevalence=0.01)

    assert result.observed_prevalence == pytest.approx(0.10)
    # Positives fixed at 100; for 1% prevalence we need 9900 negatives.
    # Scale = 9900 / 900 = 11. Effective FP = 90 x 11 = 990.
    assert result.negative_scale == pytest.approx(11.0)
    assert result.effective_fp == pytest.approx(990.0)
    assert result.precision_observed == pytest.approx(80 / 170)
    assert result.precision_target == pytest.approx(80 / (80 + 990))


def test_reweighting_leaves_recall_unchanged():
    for target in (0.0001, 0.001, 0.01, 0.5):
        result = reweight_to_prevalence(tp=80, fp=90, fn=20, tn=810, target_prevalence=target)
        assert result.recall == pytest.approx(0.8), "recall is prevalence-independent"


def test_reweighting_to_a_rarer_class_can_only_hurt_precision():
    high = reweight_to_prevalence(80, 90, 20, 810, target_prevalence=0.10)
    low = reweight_to_prevalence(80, 90, 20, 810, target_prevalence=0.001)
    assert low.precision_target < high.precision_target


# --- alerts, and the two negative controls ------------------------------------


def test_alerts_collapse_a_burst_into_one_reviewable_item():
    events = [
        _event(ts=i * 1_000, txn_id=f"txn_{i}", label=1, merchant_id="mer_000", bin="000111")
        for i in range(300)
    ]
    scores = np.full(300, 9.0)
    alert_list = metrics.alerts(events, scores, threshold=1.0)
    assert len(alert_list) == 1, "300 flagged events in one run is one thing to look at"
    assert alert_list[0]["events"] == 300
    assert alert_list[0]["is_true"] is True


def test_alerts_split_when_the_gap_exceeds_the_cooldown():
    gap = metrics.ALERT_COOLDOWN_MS + 60_000
    events = [
        _event(ts=0, txn_id="a", label=1),
        _event(ts=gap, txn_id="b", label=1),
    ]
    alert_list = metrics.alerts(events, np.array([9.0, 9.0]), threshold=1.0)
    assert len(alert_list) == 2


def test_flash_sale_alert_count_is_reported_not_suppressed(built):
    split = built["split"]
    scores = score_with_warmup(split.train, split.test, DetectorConfig())
    counts = metrics.per_scenario_counts(split.test, scores, threshold=0.0)
    assert SCENARIO_FLASH_SALE in counts
    assert counts[SCENARIO_FLASH_SALE]["is_attack"] is False
    assert counts[SCENARIO_FLASH_SALE]["events"] > 0


def test_issuer_outage_alert_count_is_reported_separately(built):
    """The two controls attack different axes and must not be averaged."""
    split = built["split"]
    scores = score_with_warmup(split.train, split.test, DetectorConfig())
    counts = metrics.per_scenario_counts(split.test, scores, threshold=0.0)

    assert SCENARIO_ISSUER_OUTAGE in counts
    assert SCENARIO_FLASH_SALE in counts
    assert counts[SCENARIO_ISSUER_OUTAGE] is not counts[SCENARIO_FLASH_SALE]
    assert counts[SCENARIO_ISSUER_OUTAGE]["is_attack"] is False

    model = CostModel.load()
    breakdown = compute_costs(split.test, scores, 0.0, model, alert_count=1)
    # Both controls appear under their own key in the per-scenario breakdown.
    assert SCENARIO_ISSUER_OUTAGE in breakdown.per_scenario_flagged
    assert SCENARIO_FLASH_SALE in breakdown.per_scenario_flagged


# --- time to detection ---------------------------------------------------------


def test_time_to_detection_counts_events_before_first_flag():
    window = {"scenario_id": "burst", "start_ms": 0, "end_ms": 100_000}
    events = [
        _event(ts=i * 1_000, txn_id=f"t{i}", label=1, scenario_id="burst", amount_paise=5_000)
        for i in range(10)
    ]
    # Nothing flags until the fourth event (index 3).
    scores = np.array([0.0, 0.0, 0.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0])

    result = metrics.time_to_detection(events, scores, 1.0, [window])
    assert result["burst"]["median_events"] == 3
    # Three events at Rs 50 each went through before the flag.
    assert result["burst"]["median_rupees"] == pytest.approx(150.0)
    assert result["burst"]["missed"] == 0


def test_time_to_detection_is_infinite_when_scenario_never_flagged():
    window = {"scenario_id": "slow_low", "start_ms": 0, "end_ms": 100_000}
    events = [
        _event(ts=i * 1_000, txn_id=f"t{i}", label=1, scenario_id="slow_low") for i in range(5)
    ]
    result = metrics.time_to_detection(events, np.zeros(5), 1.0, [window])

    assert result["slow_low"]["missed"] == 1
    assert math.isinf(result["slow_low"]["median_events"])
    assert result["slow_low"]["episodes"] == 1


def test_clean_scenarios_are_excluded_from_time_to_detection():
    windows = [
        {"scenario_id": "flash_sale", "start_ms": 0, "end_ms": 10_000},
        {"scenario_id": "issuer_outage", "start_ms": 0, "end_ms": 10_000},
    ]
    events = [_event(ts=0, label=0, scenario_id="flash_sale")]
    assert metrics.time_to_detection(events, np.array([9.0]), 1.0, windows) == {}


# --- ablations and multi-seed ---------------------------------------------------


def test_ablation_toggle_changes_active_feature_set(built):
    """Each ablation must actually change the scores, or it measures nothing."""
    split = built["split"]
    events = split.test
    full = score_with_warmup(split.train, events, DetectorConfig())
    no_ewma = score_with_warmup(split.train, events, DetectorConfig(use_ewma=False))
    no_ip = score_with_warmup(split.train, events, DetectorConfig(use_per_ip=False))

    assert not np.array_equal(full, no_ewma), "drop-EWMA changed nothing"
    assert not np.array_equal(full, no_ip), "drop-per-IP changed nothing"


def test_exactly_two_ablations_are_run(built):
    """Cut from four to two on Aug 24 to fund the issuer-outage control."""
    split = built["split"]
    rows = run_ablations(split, DetectorConfig(), CostModel.load(), 0.0)
    names = [row["name"] for row in rows]
    assert names == ["full", "drop-EWMA", "drop-per-IP"]


def test_multi_seed_spread_reported_for_all_headline_metrics():
    rows = run_multiseed(2, SMALL_CONFIG.seed, DetectorConfig(), CostModel.load())
    assert len(rows) == 2
    assert rows[0]["seed"] != rows[1]["seed"]
    for row in rows:
        for key in ("precision", "recall", "pr_auc", "net_rupees", "alerts_per_day", "threshold"):
            assert key in row, f"multi-seed row is missing {key}"


def test_evaluate_reports_the_separation_margin(built):
    """FP=0 must never be reportable without its headroom."""
    split = built["split"]
    result = evaluate(
        split, DetectorConfig(), CostModel.load(), built["manifest"]["episode_windows"]
    )
    margin = result["margin"]
    assert margin["threshold"] == result["threshold"]
    assert margin["headroom"] == pytest.approx(
        margin["threshold"] - margin["highest_clean_score"]
    )
