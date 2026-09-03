"""Phase 2 tests for the reference detector.

This module is the spec the Phase 3 Rust core is tested against, so these tests
are written to pin down *behaviour a second implementation must reproduce* — the
window-boundary convention, the memory bound, streaming/batch equivalence — not
to restate what the Python happens to do.
"""

from __future__ import annotations

import dataclasses
import json
import random
import sys
from pathlib import Path

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


def test_window_memory_bounded_per_entity(stream):
    """Retained events are bounded PER ENTITY; total memory is linear in entities.

    Renamed at the Phase 3 gate: the old name (`memory_bounded_under_entity_churn`)
    claimed more than the assertion checks. Entities are never freed, so total
    memory is O(distinct entities) - measured in docs/BENCH.md."""
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


# --- the freeze --------------------------------------------------------------


def test_detector_configuration_is_frozen_for_phase_3():
    """The reference is the Rust port's parity spec, so it must not move.

    Phase 3 tests the Rust core for numerical parity against this detector. If the
    reference's windows or feature weights change during the port, the comparison
    stops meaning anything and the parity fixture silently becomes wrong.

    These are the values the Rust core will be built against. Changing one is a
    deliberate act that should fail here first and be re-approved, not a quiet
    edit. See docs/PHASES.md, Phase 3, and docs/FAILURE_MODES.md 7 for the
    long-horizon BIN window that was diagnosed and deliberately NOT built.
    """
    config = DetectorConfig()

    assert config.window_ms == 300_000
    assert config.ring_capacity == 512
    assert config.baseline_sample_interval_ms == 60_000
    assert config.baseline_min_samples == 20
    assert config.ewma_halflife_samples == 30.0

    assert config.w_velocity_bin == 1.0
    assert config.w_decline_bin == 2.0
    assert config.w_amount == 1.0
    assert config.w_velocity_ip == 0.5
    assert config.w_repetition_damping == 1.2
    assert config.w_merchant_span_damping == 1.4


def test_the_scoring_terms_are_frozen():
    """The set of evidence and damping terms is part of the parity spec too."""
    from spandan.gen.build import build
    from helpers import SMALL_CONFIG
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        build(SMALL_CONFIG, tmp)
        events = read_stream(f"{tmp}/{TEST_FILENAME}")

    detector = ReferenceDetector(DetectorConfig(threshold=-1e18))
    flag = detector.update(events[0])
    assert flag is not None
    terms = {name for name, _ in flag.contributions}
    assert terms == {
        "velocity_bin",
        "decline_bin",
        "amount",
        "velocity_ip",
        "repetition",
        "merchant_span",
    }, f"the scoring terms changed: {sorted(terms)}"


def _parse_parity_json(text: str) -> tuple[dict, list[float], float]:
    doc = json.loads(text)
    scores = doc.pop("expected_scores")
    return doc, scores, float(doc["tolerance"])


def _parse_parity_tsv(text: str) -> tuple[dict, list[float], float]:
    lines = text.splitlines()
    head, rows = lines[:3], [line.rsplit("	", 1) for line in lines[3:]]
    meta = {"head": head, "events": [cells for cells, _ in rows]}
    return meta, [float(score) for _, score in rows], float(head[0].split("	")[1])


def _assert_fixture_current(on_disk: str, regenerated: str, parse, stale_message: str) -> None:
    """Exact bytes on the platform that wrote the fixture; the same data within
    the tolerance the fixture itself declares everywhere else.

    The fixtures were generated on Windows. Under glibc the pure-Python
    reference regenerates 640 of the 3,866 scores with last-bit differences,
    the largest 2.8e-14, and every non-score field identical (BUILD_LOG,
    2026-09-03): `math.exp` and `math.log` round differently across C
    runtimes. The detector did not change; the fixture is exact to a libm.
    A stale fixture still fails on every platform - a real detector change
    moves scores by far more than the 1e-9 the fixture allows.
    """
    if sys.platform == "win32":
        assert on_disk == regenerated, stale_message
        return
    disk_meta, disk_scores, tolerance = parse(on_disk)
    new_meta, new_scores, _ = parse(regenerated)
    assert disk_meta == new_meta, "a non-score field of the fixture differs; " + stale_message
    assert len(disk_scores) == len(new_scores), stale_message
    worst = max(abs(a - b) for a, b in zip(disk_scores, new_scores))
    assert worst <= tolerance, f"scores drift by {worst:.3e} > {tolerance:.0e}; " + stale_message


def test_parity_fixture_is_current():
    """The committed parity fixture must be what the frozen reference produces.

    If the reference detector ever changes, parity.json would silently describe old
    Python, and the parity test would compare the port to a stale artifact while
    still passing. This test fails first.
    """
    from spandan.detect import parity

    fixture_path = Path(__file__).resolve().parent / "fixtures" / "parity.json"
    assert fixture_path.exists(), "tests/fixtures/parity.json is missing; it must be committed"

    _assert_fixture_current(
        fixture_path.read_text(encoding="utf-8"),
        parity.serialise(parity.build_fixture()),
        _parse_parity_json,
        "the parity fixture is stale - the reference detector changed after it was "
        "written. Either revert the detector (it is FROZEN through Phase 3) or "
        "regenerate with `python -m spandan.detect.parity` and re-approve.",
    )


def test_parity_fixture_is_current_tsv():
    """The TSV twin the Rust test reads must be as fresh as the JSON.

    cargo test replays parity.tsv, not parity.json; a stale TSV would let the
    Rust core pass parity against an old reference while the JSON said otherwise.
    """
    from spandan.detect import parity

    tsv_path = Path(__file__).resolve().parent / "fixtures" / "parity.tsv"
    assert tsv_path.exists(), "tests/fixtures/parity.tsv is missing; cargo test reads it"

    _assert_fixture_current(
        tsv_path.read_text(encoding="utf-8"),
        parity.serialise_tsv(parity.build_fixture()),
        _parse_parity_tsv,
        "parity.tsv is stale relative to the frozen reference - regenerate with "
        "`python -m spandan.detect.parity` and re-approve",
    )


def test_parity_json_and_tsv_agree():
    """The two serialisations must carry identical data, so they cannot drift.

    Promised in parity.py's docstring. The JSON is the canonical human-readable
    artifact; the TSV exists because Phase 3 approved exactly one Rust crate
    (proptest) and reading JSON would have needed serde_json.
    """
    import json

    fixtures = Path(__file__).resolve().parent / "fixtures"
    doc = json.loads((fixtures / "parity.json").read_text(encoding="utf-8"))

    lines = (fixtures / "parity.tsv").read_text(encoding="utf-8").splitlines()
    header = {line.split("	")[0]: line.split("	")[1:] for line in lines[:3]}
    rows = [line.split("	") for line in lines[3:]]

    assert float(header["tolerance"][0]) == doc["tolerance"]
    assert header["columns"][:-1] == doc["feature_columns"]
    assert header["columns"][-1] == "expected_score"
    assert len(rows) == len(doc["events"]) == len(doc["expected_scores"])

    for tsv_row, json_row, score in zip(rows, doc["events"], doc["expected_scores"]):
        assert tsv_row[:-1] == [str(v) for v in json_row]
        assert float(tsv_row[-1]) == score

    config = dict(cell.split("=", 1) for cell in header["config"])
    for key, value in doc["detector_config"].items():
        assert key in config, f"TSV config is missing {key}"
        if isinstance(value, bool):
            assert config[key] == ("true" if value else "false")
        else:
            assert float(config[key]) == float(value)


def test_parity_fixture_carries_no_labels():
    """The Rust core never sees labels, so its contract must not contain them."""
    import json

    fixture_path = Path(__file__).resolve().parent / "fixtures" / "parity.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    from spandan.gen.schema import FEATURE_COLUMNS, LABEL_COLUMNS

    assert fixture["feature_columns"] == list(FEATURE_COLUMNS)
    for banned in LABEL_COLUMNS:
        assert banned not in fixture["feature_columns"]
    assert len(fixture["events"]) == len(fixture["expected_scores"])
    assert all(len(row) == len(FEATURE_COLUMNS) for row in fixture["events"])


def test_parity_fixture_saturates_the_ring_buffers():
    """The Python twin of the Rust-side coverage guard.

    Phase 3 review: a fixture that never fills a ring proves parity only on the
    happy path. The mega-burst episode overflows all four axes' rings inside one
    window; if a regeneration ever loses it, this fails before the weaker
    fixture is committed.
    """
    from spandan.detect.parity import PARITY_CONFIG
    from spandan.gen.build import generate_events

    events = generate_events(PARITY_CONFIG)
    detector = ReferenceDetector(DetectorConfig())
    detector.score_batch(events)

    saturated_axes = {
        axis for (axis, _key), state in detector._state.items() if state.saturated
    }
    assert saturated_axes == {"bin", "ip", "device", "merchant"}, (
        f"fixture saturates only {sorted(saturated_axes)}; ring wraparound must be "
        "exercised on every axis"
    )
