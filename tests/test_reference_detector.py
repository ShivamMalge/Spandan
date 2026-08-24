"""Phase 2 tests for the reference detector.

This module is the spec the Phase 3 Rust core is tested against, so these tests
are written to pin down *behaviour a second implementation must reproduce* — the
window-boundary convention, the memory bound, streaming/batch equivalence — not
to restate what the Python happens to do.
"""

from __future__ import annotations

import dataclasses
import random

import numpy as np
import pytest

from helpers import SMALL_CONFIG  # noqa: E402
from spandan.detect import DetectorConfig, ReferenceDetector
from spandan.detect.reference import EntityState, Welford
from spandan.gen.build import TEST_FILENAME, TRAIN_FILENAME, build, read_stream
from spandan.gen.schema import STATUS_APPROVED, STATUS_DECLINED, Event


@pytest.fixture(scope="session")
def stream(tmp_path_factory):
    out = tmp_path_factory.mktemp("detstream")
    build(SMALL_CONFIG, out)
    return {
        "train": read_stream(out / TRAIN_FILENAME),
        "test": read_stream(out / TEST_FILENAME),
    }


# --- the estimators ---------------------------------------------------------


def test_welford_matches_two_pass():
    rng = random.Random(1234)
    values = [rng.uniform(-500, 500) for _ in range(2000)]

    welford = Welford()
    for value in values:
        welford.update(value)

    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)

    assert welford.mean == pytest.approx(mean, rel=1e-12, abs=1e-9)
    assert welford.variance == pytest.approx(variance, rel=1e-10, abs=1e-9)


def test_welford_survives_a_large_offset_where_naive_variance_fails():
    """The reason Welford is here rather than sum-of-squares.

    A naive E[x^2] - E[x]^2 loses catastrophic precision at a large offset. This
    is the property Phase 3's property test must also hold.
    """
    rng = random.Random(99)
    values = [1e9 + rng.uniform(-1, 1) for _ in range(5000)]

    welford = Welford()
    for value in values:
        welford.update(value)

    mean = sum(values) / len(values)
    two_pass = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    assert welford.variance == pytest.approx(two_pass, rel=1e-6)
    assert welford.variance > 0.0


# --- the window convention --------------------------------------------------


def test_window_counts_match_bruteforce(stream):
    """Window is (t - W, t]. Recomputed from scratch, not read off the state."""
    config = DetectorConfig()
    events = stream["test"][:4000]

    state = EntityState(capacity=100_000, halflife=config.ewma_halflife_samples)
    for i, event in enumerate(events):
        state.evict_before(event.ts - config.window_ms)
        state.push(event.ts, event.status == STATUS_DECLINED, float(event.amount_paise),
                   event.card_ref, event.merchant_id)

        if i % 400 == 0:
            expected = [
                e for e in events[: i + 1] if event.ts - config.window_ms < e.ts <= event.ts
            ]
            assert len(state.events) == len(expected), f"window mismatch at event {i}"


def test_decline_ratio_matches_bruteforce(stream):
    config = DetectorConfig()
    events = stream["test"][:4000]

    state = EntityState(capacity=100_000, halflife=config.ewma_halflife_samples)
    for i, event in enumerate(events):
        state.evict_before(event.ts - config.window_ms)
        state.push(event.ts, event.status == STATUS_DECLINED, float(event.amount_paise),
                   event.card_ref, event.merchant_id)

        if i % 400 == 0:
            window = [
                e for e in events[: i + 1] if event.ts - config.window_ms < e.ts <= event.ts
            ]
            expected = sum(1 for e in window if e.status == STATUS_DECLINED)
            assert state.declines_in_window == expected


def test_event_exactly_one_window_old_has_fallen_out():
    """Pins the half-open boundary, which is the classic parity day-eater."""
    state = EntityState(capacity=64, halflife=30.0)
    state.push(1_000, False, 100.0, "card_a", "mer_a")
    state.push(2_000, False, 100.0, "card_b", "mer_b")

    # Window width 1000 evaluated at t=2000 covers (1000, 2000].
    state.evict_before(2_000 - 1_000)
    stamps = [row[0] for row in state.events]
    assert stamps == [2_000], "the event exactly W old should have been evicted"


# --- the memory bound -------------------------------------------------------


def test_ring_buffer_never_exceeds_capacity():
    capacity = 32
    state = EntityState(capacity=capacity, halflife=30.0)
    for i in range(5_000):
        state.push(i, i % 3 == 0, float(i), f"card_{i}", "mer_a")
        assert len(state.events) <= capacity
    assert state.saturated, "overflowing the ring must be recorded, not silent"


def test_saturation_keeps_the_decline_counter_consistent():
    """The counter is maintained incrementally, so eviction must decrement it."""
    capacity = 16
    state = EntityState(capacity=capacity, halflife=30.0)
    for i in range(400):
        state.push(i, i % 2 == 0, 100.0, f"card_{i}", "mer_a")
    assert state.declines_in_window == sum(1 for row in state.events if row[1])


def test_memory_bounded_under_entity_churn(stream):
    """Total state grows with distinct entities, never with event count."""
    detector = ReferenceDetector(DetectorConfig())
    events = stream["train"]
    detector.score_batch(events)

    total_buffered = sum(len(state.events) for state in detector._state.values())
    assert total_buffered <= len(detector._state) * DetectorConfig().ring_capacity
    assert total_buffered < len(events), "state should not retain the whole stream"


# --- determinism and the two surfaces ---------------------------------------


def test_deterministic_across_runs(stream):
    config = DetectorConfig()
    first = ReferenceDetector(config).score_batch(stream["test"])
    second = ReferenceDetector(config).score_batch(stream["test"])
    assert np.array_equal(first, second)


def test_streaming_matches_batch(stream):
    """`update` and `score_batch` must be the same state machine, exactly.

    Phase 4 reuses this against the Rust core, where a drift between the two
    surfaces would be a real bug rather than a rounding difference.
    """
    config = DetectorConfig(threshold=-1e18)  # flag everything, so update returns a score
    events = stream["test"][:3000]

    batch = ReferenceDetector(config).score_batch(events)

    detector = ReferenceDetector(config)
    streamed = np.array([detector.update(event).score for event in events])

    assert np.array_equal(batch, streamed)


def test_flag_contributions_sum_to_the_score(stream):
    """An explanation can never assert a cause the arithmetic does not support.

    Phase 5 builds analyst-facing text out of these terms, so they have to add up
    to the score they are explaining.
    """
    config = DetectorConfig(threshold=5.0)
    detector = ReferenceDetector(config)
    checked = 0
    for event in stream["test"]:
        flag = detector.update(event)
        if flag is None:
            continue
        assert sum(value for _, value in flag.contributions) == pytest.approx(flag.score)
        checked += 1
        if checked >= 50:
            break
    assert checked > 0, "no flags produced; the test asserted nothing"


# --- the label boundary -----------------------------------------------------


def test_detector_cannot_see_labels(stream):
    """Scores must be bit-identical when labels and scenario ids are scrambled.

    Stronger than reviewing the code for `event.label`: it would catch a leak
    through any path, including one added later.
    """
    rng = random.Random(7)
    events = stream["test"]
    scrambled = [
        dataclasses.replace(e, label=rng.randint(0, 1), scenario_id="scrambled")
        for e in events
    ]

    config = DetectorConfig()
    original = ReferenceDetector(config).score_batch(events)
    altered = ReferenceDetector(config).score_batch(scrambled)

    assert np.array_equal(original, altered)


def test_no_card_novelty_state_is_retained():
    """The binding constraint from gen/ASSUMPTIONS.md 1.7a, at runtime.

    Card repetition is measured inside the current window only. Feeding the same
    events with every card renamed to something never seen before must not change
    a single score - if it did, the detector would be keying on novelty and the
    flash-sale control would stop being valid.
    """
    base = [
        Event(
            ts=1_000 * i,
            txn_id=f"txn_{i}",
            merchant_id="mer_000",
            bin="000123",
            card_ref=f"card_{i % 7:010d}",
            ip="192.0.2.5",
            device_id="dev_0000000001",
            amount_paise=15_000,
            status=STATUS_DECLINED if i % 4 else STATUS_APPROVED,
            label=0,
            scenario_id="benign",
        )
        for i in range(600)
    ]
    renamed = [
        dataclasses.replace(e, card_ref=f"card_{9_000_000 + int(e.card_ref[5:]):010d}")
        for e in base
    ]

    config = DetectorConfig()
    assert np.array_equal(
        ReferenceDetector(config).score_batch(base),
        ReferenceDetector(config).score_batch(renamed),
    )
