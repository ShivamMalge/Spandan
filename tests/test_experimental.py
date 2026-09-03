"""Phase E tests: the long-horizon experiment is a subclass that cannot move
the reference.

Two properties the plan named, plus two that pin what the subclass is allowed
to change: only the repetition term, and only once the windows diverge.
"""

from __future__ import annotations

import numpy as np
import pytest

from helpers import SMALL_CONFIG  # noqa: E402
from spandan.detect import DetectorConfig, ReferenceDetector
from spandan.detect.experimental import LONG_WINDOW_MS, LongHorizonConfig, LongHorizonDetector
from spandan.eval.loader import load_split
from spandan.gen.build import build


@pytest.fixture(scope="module")
def events(tmp_path_factory):
    out = tmp_path_factory.mktemp("experimentstream")
    build(SMALL_CONFIG, out)
    split = load_split(out)
    return split.train_warmup + split.validation + split.test


def test_long_horizon_reduces_to_reference_when_window_disabled(events):
    reference = ReferenceDetector(DetectorConfig()).score_batch(events)
    disabled = LongHorizonDetector(LongHorizonConfig(enabled=False)).score_batch(events)
    assert np.array_equal(reference, disabled)


def test_long_horizon_scores_match_reference_on_first_five_minutes(events):
    """Inside the first five minutes of a BIN's history both windows hold the
    same events, so the repetition term is the same number and the score is
    identical, bit for bit. After that the windows diverge and so must some
    scores, or the experiment measures nothing."""
    reference = ReferenceDetector(DetectorConfig())
    long_horizon = LongHorizonDetector(LongHorizonConfig(w_repetition_damping=1.2))
    first_seen: dict[str, int] = {}
    same, differ = 0, 0
    for event in events:
        first_seen.setdefault(event.bin, event.ts)
        r_score, r_evidence = reference._advance(event)
        l_score, l_evidence = long_horizon._advance(event)
        young = event.ts - first_seen[event.bin] < DetectorConfig().window_ms
        if young and not r_evidence["window_saturated"]:
            assert l_score == r_score, (event.txn_id, l_score, r_score)
            same += 1
        elif l_score != r_score:
            differ += 1
    # The small stream has few BINs and each contributes only its first five
    # minutes; 64 qualifying events on the fixture stream, all bit-identical.
    assert same >= 30, f"too few in-window events to have tested anything ({same})"
    assert differ > 0, "the long window never changed a score"
    assert LONG_WINDOW_MS == 3_600_000


def test_long_horizon_changes_only_the_repetition_term(events):
    """Every other term, and every other evidence field the reference reports,
    is the reference's own value on every event."""
    reference = ReferenceDetector(DetectorConfig())
    long_horizon = LongHorizonDetector(LongHorizonConfig(w_repetition_damping=6.0))
    for event in events[:20_000]:
        _, r_evidence = reference._advance(event)
        _, l_evidence = long_horizon._advance(event)
        r_terms = dict(r_evidence.pop("terms"))
        l_terms = dict(l_evidence.pop("terms"))
        r_terms.pop("repetition")
        l_terms.pop("repetition")
        assert l_terms == r_terms
        for key, value in r_evidence.items():
            assert l_evidence[key] == value, key
        assert l_terms.keys() == r_terms.keys()


def test_reference_module_is_not_imported_differently_by_the_experiment():
    """Importing the experiment must not monkeypatch or rebind anything on the
    reference: the class the harness scores with is the same object."""
    import spandan.detect.reference as reference_module

    assert LongHorizonDetector.__mro__[1] is reference_module.ReferenceDetector
    assert ReferenceDetector._score is reference_module.ReferenceDetector._score
    assert LongHorizonDetector._score is not ReferenceDetector._score
    overrides = {k for k, v in LongHorizonDetector.__dict__.items() if callable(v) and not k.startswith("__")}
    assert overrides == {"reset", "_advance", "_score"}
    assert "__init__" in LongHorizonDetector.__dict__
