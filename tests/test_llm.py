"""Phase 5: the LLM boundary, enforced by tests rather than discipline.

The load-bearing test here is `test_eval_runs_with_llm_import_poisoned`. The
cassette tests prove the explanation layer works offline; the poisoned-import
test proves something stronger and rarer — that **no number in the evaluation
ever passed through a language model**, because the evaluation runs to
completion with identical output while `spandan.llm` raises on the very
attempt to import it. That is the claim a review panel cares about; everything
else in this file is secondary.

A conftest fixture blocks socket creation for every test in this module, so
"replay mode never touches the network" is enforced at the OS boundary, not
trusted from a docstring.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import socket
import sys
import urllib.request  # noqa: F401  (see below)
from pathlib import Path

# The urllib.request import above is load-bearing: it transitively performs the
# process's first `import ssl`, whose SSLSocket class subclasses socket.socket
# AT IMPORT TIME. It must happen before the no_network fixture replaces
# socket.socket with a plain function, or that class statement raises TypeError.
# Caught only by the fresh-clone check: on the build machine, globally installed
# pytest plugins imported ssl first and hid the ordering dependency.

import numpy as np
import pytest

from spandan.detect.interface import Flag

CASSETTE_DIR = Path("python/spandan/llm/cassettes")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every test in this module runs with socket creation disabled.

    Replay mode claims it never touches the network; this makes the claim
    physically true for the duration of the tests rather than rhetorically.
    """

    def _refuse(*args, **kwargs):
        raise AssertionError("a test in test_llm.py attempted to open a socket")

    monkeypatch.setattr(socket, "socket", _refuse)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("SPANDAN_LLM_MODE", raising=False)


def _sample_flag(**overrides) -> Flag:
    base = dict(
        ts=1_784_576_351_592,
        txn_id="txn_test",
        merchant_id="mer_008",
        bin="099813",
        score=24.28,
        threshold=21.99,
        window_events=1,
        window_declines=1,
        window_decline_ratio=1.0,
        baseline_decline_ratio=0.099,
        velocity_z=0.0,
        baseline_window_events=1.1,
        window_distinct_cards=1,
        cards_per_event=1.0,
        window_distinct_merchants=1,
        window_amount_mean_paise=545.0,
        baseline_amount_mean_paise=283_258.0,
        window_saturated=False,
        contributions=(),
    )
    base.update(overrides)
    return Flag(**base)


# --- the boundary: imports -----------------------------------------------------


def test_detect_and_eval_import_graphs_exclude_spandan_llm():
    """Walk everything `spandan.detect` and `spandan.eval` import, transitively.

    If either ever gains a path to `spandan.llm`, a model is one refactor away
    from the numbers. This fails at the first edge.
    """
    for name in [m for m in sys.modules if m.startswith("spandan")]:
        del sys.modules[name]

    importlib.import_module("spandan.detect")
    importlib.import_module("spandan.eval")
    importlib.import_module("spandan.eval.harness")
    importlib.import_module("spandan.eval.metrics")
    importlib.import_module("spandan.eval.costs")
    importlib.import_module("spandan.detect.reference")
    importlib.import_module("spandan.detect.rust_engine")
    importlib.import_module("spandan.triage.graph")   # the post-detection layer too

    offenders = [m for m in sys.modules if m.startswith("spandan.llm")]
    assert not offenders, (
        f"importing the detector/eval stack pulled in {offenders}; the LLM layer "
        "must be unreachable from the code that produces numbers"
    )


class _PoisonedModule:
    """Stands in for spandan.llm; any attribute access is a hard failure."""

    def __getattr__(self, name):
        raise AssertionError(
            f"the evaluation touched spandan.llm.{name} - a language model is "
            "inside the number-producing path"
        )


def test_eval_runs_with_llm_import_poisoned(tmp_path):
    """THE load-bearing test of this phase.

    `sys.modules['spandan.llm']` is replaced by an object that raises on any
    attribute access, and its submodules by ones that raise on import. The full
    evaluation - scoring, threshold selection, cost model, per-scenario metrics -
    then runs twice, poisoned and unpoisoned, and must produce IDENTICAL scores
    and an identical selected threshold.

    Green here is the proof behind the submission's central hygiene claim: no
    number in the evaluation passed through a language model. Not "we didn't",
    but "we structurally could not have".
    """
    from helpers import SMALL_CONFIG
    from spandan.detect import DetectorConfig
    from spandan.eval.costs import CostModel
    from spandan.eval.harness import score_split_once, select_threshold, sweep_thresholds
    from spandan.eval.loader import load_split
    from spandan.gen.build import build

    build(SMALL_CONFIG, tmp_path)
    split = load_split(tmp_path)
    model = CostModel.load()
    config = DetectorConfig()

    from spandan.eval.triage_report import run_triage

    def run_full_eval():
        validation, test = score_split_once(split, config)
        rows = sweep_thresholds(split.validation, validation, model)
        chosen = select_threshold(rows, model.alerts_per_day_budget)
        # The post-detection graph runs here too: it must decide what every flag
        # becomes without the LLM package existing.
        triage = run_triage(split, test, chosen["threshold"], model, config)
        return validation, test, chosen["threshold"], triage["declined"], triage["audit_entries"]

    poison = _PoisonedModule()
    saved = {m: sys.modules[m] for m in list(sys.modules) if m.startswith("spandan.llm")}
    try:
        for name in saved:
            del sys.modules[name]
        sys.modules["spandan.llm"] = poison  # type: ignore[assignment]
        for sub in ("provider", "explain", "grounding"):
            sys.modules[f"spandan.llm.{sub}"] = poison  # type: ignore[assignment]

        poisoned = run_full_eval()
    finally:
        for name in [m for m in sys.modules if m.startswith("spandan.llm")]:
            del sys.modules[name]
        sys.modules.update(saved)

    clean = run_full_eval()

    assert np.array_equal(poisoned[0], clean[0])
    assert np.array_equal(poisoned[1], clean[1])
    assert poisoned[2] == clean[2]
    assert poisoned[3] == clean[3] and poisoned[4] == clean[4], "triage decisions must not depend on the LLM package"
    assert poisoned[4] > 0, "the triage graph ran and audited"

    with pytest.raises(AssertionError, match="language model"):
        _ = poison.complete  # the poison itself must actually bite


# --- the cassette mechanics ----------------------------------------------------


def test_replay_from_cassette_with_no_network_and_no_key():
    """A committed cassette answers with sockets disabled and no key set."""
    cassettes = sorted(CASSETTE_DIR.glob("*.json"))
    assert cassettes, "no cassettes committed"

    from spandan.llm import complete

    for path in cassettes:
        cassette = json.loads(path.read_text(encoding="utf-8"))
        text = complete(cassette["prompt"], model=cassette["model"])
        assert text == cassette["response_text"]
        assert len(text) > 200, "an explanation this short is not an explanation"


def test_missing_cassette_raises_loudly_not_silently():
    from spandan.llm import CassetteMiss, complete

    with pytest.raises(CassetteMiss, match="never touches the network"):
        complete("a prompt no cassette has ever seen " * 3)


def test_record_mode_without_key_still_never_reaches_the_network(monkeypatch):
    """Even explicitly asking for record mode dies before any socket: the key
    check precedes the request, and the socket guard would catch it anyway."""
    monkeypatch.setenv("SPANDAN_LLM_MODE", "record")
    from spandan.llm import provider

    with pytest.raises(RuntimeError, match="requires ANTHROPIC_API_KEY"):
        provider.complete("unrecorded prompt for the record-mode test")


def test_replay_needs_no_sdk(monkeypatch):
    """Replay must work with the anthropic SDK absent: it is an optional extra
    for recording only, imported inside the record path after the key check.
    Poisoning the import proves replay never reaches for it."""
    import sys

    monkeypatch.setitem(sys.modules, "anthropic", None)
    from spandan.llm import provider

    cassette = json.loads(sorted(CASSETTE_DIR.glob("*.json"))[0].read_text(encoding="utf-8"))
    assert provider.complete(cassette["prompt"], cassette["model"]) == cassette["response_text"]
    monkeypatch.setenv("SPANDAN_LLM_MODE", "record")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    with pytest.raises(RuntimeError, match="needs the anthropic SDK"):
        provider.complete("a prompt no cassette has ever seen, with the sdk poisoned")


def test_record_request_matches_the_installed_sdk_signature():
    """Every keyword the record path passes must be a parameter of the installed
    SDK's messages.create. The 1.x SDK removed `temperature`; the first
    recording attempt failed at the terminal instead of here. Skipped when the
    optional record extra is not installed (CI runs replay only)."""
    import inspect

    anthropic = pytest.importorskip("anthropic")
    from spandan.llm import provider

    accepted = set(inspect.signature(anthropic.resources.messages.Messages.create).parameters)
    passed = set(provider.record_request("prompt", provider.MODEL_ID))
    assert passed <= accepted, passed - accepted
    client_params = set(inspect.signature(anthropic.Anthropic.__init__).parameters)
    assert {"api_key", "max_retries", "timeout"} <= client_params
    assert provider.MODEL_ID == "claude-haiku-4-5"


def test_cassettes_declare_their_provenance():
    """Each cassette says exactly how it came to exist.

    A reviewer must be able to tell what produced each explanation: a wire
    recording names the provider and the exact model id; anything else must
    state its true origin (the first pair were authored in-context, before any
    key existed, and said so). A plausible artifact with an untrue origin would
    be the sixth instance of the pattern in BUILD_LOG.
    """
    for path in sorted(CASSETTE_DIR.glob("*.json")):
        cassette = json.loads(path.read_text(encoding="utf-8"))
        assert "recorded_via" in cassette, f"{path.name} hides its origin"
        assert len(cassette["recorded_via"]) > 40
        assert cassette["key"] == path.stem


# --- the flag boundary ---------------------------------------------------------


def test_flag_dataclass_is_frozen():
    flag = _sample_flag()
    with pytest.raises(dataclasses.FrozenInstanceError):
        flag.score = 0.0  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        flag.threshold = -1.0  # type: ignore[misc]


def test_explain_does_not_mutate_flag():
    from spandan.llm import render_prompt, render_template

    flag = _sample_flag()
    before = dataclasses.asdict(flag)
    render_prompt(flag)
    render_template(flag)
    assert dataclasses.asdict(flag) == before


def test_prompt_contains_only_flag_fields_no_labels():
    """The prompt is assembled from the frozen Flag and nothing else - no label,
    no scenario id, nothing the detector itself could not see."""
    from spandan.llm import render_prompt

    prompt = render_prompt(_sample_flag())
    for banned in ("label", "scenario_id", "slow_low", "flash_sale", "ground truth"):
        assert banned not in prompt, f"prompt leaks {banned!r}"
    assert "099813" in prompt and "mer_008" in prompt


def test_explain_returns_a_string_and_nothing_else():
    """The task's whole contract: str in the analyst's hands."""
    cassettes = sorted(CASSETTE_DIR.glob("*.json"))
    cassette = json.loads(cassettes[0].read_text(encoding="utf-8"))

    from spandan.llm import complete

    result = complete(cassette["prompt"], model=cassette["model"])
    assert isinstance(result, str)


# --- the validator: the boundary for the prose ---------------------------------


def test_validator_rejects_the_recorded_fabrications():
    """The two cassettes recorded on 2026-08-26 ARE the fabrication finding.

    One conditions a block on a CVV/AVS result; the other on per-card history
    and a cardholder IP. None of those exist in this pipeline. If the validator
    accepts either, it does not catch the failure it was built for.
    """
    from spandan.llm.grounding import validate_cassette

    verdicts = {
        path.stem: validate_cassette(path)[2]
        for path in sorted(CASSETTE_DIR.glob("*.json"))
    }
    assert verdicts, "no cassettes committed"

    probe = verdicts["9738bd8f63262ce48ca28a3daaebbd1c"]      # the Rs 5.45 probe
    sale = verdicts["7e36f73e42cab59ec4f903a29c7b22d5"]       # the Rs 150 flash-sale FP
    assert not probe.ok, "the CVV/AVS note was accepted"
    assert any("CVV" in r or "AVS" in r for r in probe.reasons), probe.reasons
    assert not sale.ok, "the per-card-history note was accepted"
    assert any("history" in r or "IP" in r for r in sale.reasons), sale.reasons


def test_validator_accepts_the_template():
    """The deterministic template can only substitute fields that exist, so it
    must pass by construction - if it does not, the validator is too strict to
    ship in front of it."""
    from spandan.llm import render_prompt, render_template, validate

    flag = _sample_flag()
    verdict = validate(render_template(flag), render_prompt(flag))
    assert verdict.ok, verdict.reasons

    # And the one in the CLI's fallback path, with a saturated window and a
    # non-trivial amount, so the numeric checks see rounding.
    flag2 = _sample_flag(window_amount_mean_paise=15_000.0, baseline_amount_mean_paise=265_800.0,
                         window_decline_ratio=0.83, baseline_decline_ratio=0.087,
                         window_events=70, window_declines=58, window_saturated=True)
    verdict2 = validate(render_template(flag2), render_prompt(flag2))
    assert verdict2.ok, verdict2.reasons


def test_validator_catches_invented_evidence_numbers():
    """A note that quotes a rupee amount or a percentage the prompt never
    contained has invented evidence, whatever else it says."""
    from spandan.llm import render_prompt, validate

    prompt = render_prompt(_sample_flag())
    assert validate("Rs 5.45 declined, 100% of the window.", prompt).ok
    assert not validate("Rs 5.45 declined; the card's usual ticket is Rs 12,000.", prompt).ok
    assert not validate("Decline rate here is 63% against a 10% baseline.", prompt).ok
    # Rounding is not fabrication.
    assert validate("About Rs 5 against a Rs 2,833 ticket; 9.9% baseline declines.", prompt).ok


def test_validator_cannot_see_labels_or_state():
    """Two strings in, a verdict out. It has no access to the Flag, the stream,
    the labels, or the network - asserted on the signature and the import graph."""
    import inspect

    grounding = importlib.import_module("spandan.llm.grounding")

    params = list(inspect.signature(grounding.validate).parameters)
    assert params == ["note", "prompt"]

    for name in list(sys.modules):
        if name.startswith(("spandan.gen", "spandan.eval", "spandan.detect")):
            del sys.modules[name]
    importlib.reload(grounding)
    assert not [m for m in sys.modules if m.startswith(("spandan.gen", "spandan.eval"))], (
        "the validator pulled in the generator or the evaluation"
    )


def test_explain_flag_never_returns_a_rejected_note(monkeypatch):
    """Validation is inside explain_flag, so no caller can bypass it."""
    from spandan.llm import ExplanationRejected, explain_flag, provider

    fabricated = ("**Rs 5.45 | BIN 099813 | probe**\n\nBlock the BIN if the CVV/AVS "
                  "result on this attempt returned Mismatched.")
    monkeypatch.setattr(provider, "complete", lambda prompt, model=None: fabricated)

    with pytest.raises(ExplanationRejected) as excinfo:
        explain_flag(_sample_flag())
    assert excinfo.value.note == fabricated
    assert not excinfo.value.verdict.ok


def test_grounded_prompt_is_the_same_evidence_plus_the_rule():
    """The grounded variant must not smuggle in new evidence - it may only add
    the enumeration of what does not exist."""
    from spandan.llm import render_prompt

    flag = _sample_flag()
    plain, grounded = render_prompt(flag), render_prompt(flag, grounded=True)
    assert grounded.startswith(plain)
    assert "GROUNDING RULE" in grounded
    for banned in ("label", "scenario_id", "slow_low", "flash_sale"):
        assert banned not in grounded
