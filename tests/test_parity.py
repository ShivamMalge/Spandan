"""Phase 4 cross-engine parity: the Rust core behind the Python seam.

The committed fixture (Phase 3) proved the cores agree on a 3,866-event stream.
These tests prove the *integration* agrees — columnarisation, the zero-copy
boundary, config plumbing — on freshly generated data at full state depth. A
divergence here after a bit-exact fixture means the fixture was under-covering,
which is exactly the finding the Phase 3 review said to go looking for.
"""

from __future__ import annotations

import numpy as np
import pytest

from helpers import SMALL_CONFIG  # noqa: E402
from spandan.detect import DetectorConfig, ReferenceDetector
from spandan.detect.rust_engine import RustDetector, columnarise, make_detector
from spandan.gen.build import TEST_FILENAME, TRAIN_FILENAME, build, read_stream

#: Cross-engine tolerance. Zero, deliberately: the Phase 3 fixture came out
#: bit-exact, so anything looser here would be accepting a regression the
#: project already knows it does not have to accept.
TOLERANCE = 0.0


@pytest.fixture(scope="session")
def stream(tmp_path_factory):
    out = tmp_path_factory.mktemp("xengine")
    build(SMALL_CONFIG, out)
    return {
        "train": read_stream(out / TRAIN_FILENAME),
        "test": read_stream(out / TEST_FILENAME),
    }


def test_rust_python_scores_within_tolerance(stream):
    """Named in the PHASES.md Phase 4 acceptance criteria."""
    events = stream["train"] + stream["test"]
    config = DetectorConfig()

    python_scores = ReferenceDetector(config).score_batch(events)
    rust_scores = RustDetector(config).score_batch(events)

    delta = np.abs(python_scores - rust_scores).max()
    assert delta <= TOLERANCE, (
        f"engines diverge by {delta:e} on {len(events)} events; the Phase 3 "
        "fixture was bit-exact, so this is an integration bug, not float noise"
    )


def test_rust_python_agree_on_ablated_configs(stream):
    """The ablation switches cross the FFI too, and a silently-ignored kwarg
    would make ENGINE=rust ablation rows quietly wrong."""
    events = stream["test"]
    for variant in (
        DetectorConfig(use_ewma=False),
        DetectorConfig(use_per_ip=False),
        DetectorConfig(window_ms=60_000, ring_capacity=64),
    ):
        py = ReferenceDetector(variant).score_batch(events)
        rs = RustDetector(variant).score_batch(events)
        assert np.array_equal(py, rs), f"engines diverge under {variant}"


def test_streaming_update_matches_score_batch(stream):
    """Named in the PHASES.md Phase 4 acceptance criteria — on the Rust engine,
    through the Python-facing surface."""
    events = stream["test"][:3_000]
    config = DetectorConfig()

    batch = RustDetector(config).score_batch(events)

    streaming = RustDetector(config)
    streamed = np.array([streaming.update(event) for event in events])

    assert np.array_equal(batch, streamed)


def test_zero_copy_does_not_mutate_input_array(stream):
    """Named in the PHASES.md Phase 4 acceptance criteria.

    `score_batch` borrows the numeric columns without copying; a core that wrote
    through the borrow would corrupt the caller's arrays. Byte-compare the
    columns before and after.
    """
    events = stream["test"][:5_000]
    columns = columnarise(events)
    before = {
        name: columns[name].copy() for name in ("ts", "amount_paise", "declined")
    }

    detector = RustDetector(DetectorConfig())
    detector._native.score_batch(**columns)

    for name, saved in before.items():
        assert np.array_equal(columns[name], saved), f"{name} was mutated"
        assert columns[name].dtype == saved.dtype


def test_make_detector_rejects_unknown_engines():
    with pytest.raises(ValueError, match="unknown engine"):
        make_detector("cuda")


def test_rust_engine_exposes_the_memory_introspection(stream):
    """entity_count / buffered_events / saturated_entities back the BENCH.md
    memory measurement; if they vanish the measurement silently dies."""
    detector = RustDetector(DetectorConfig())
    detector.score_batch(stream["test"][:2_000])

    assert detector.entity_count() > 0
    assert detector.buffered_events() > 0
    assert detector.buffered_events() <= detector.entity_count() * 512
    assert detector.saturated_entities() >= 0
