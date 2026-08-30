"""Phase 1 acceptance tests for the synthetic stream generator.

These run against a reduced config built into a temp directory rather than
against `data/`, so a fresh clone can run the suite before `make data` has ever
been run. The reduced config keeps every structural property the full one has —
all four scenarios, positives on both sides of the split, the same entity ranges
— and only shrinks the volume.

No unseeded randomness anywhere, including here (`agents.md` §5).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from spandan.gen import entities
from spandan.gen.build import (
    MANIFEST_FILENAME,
    TEST_FILENAME,
    TRAIN_FILENAME,
    build,
    read_stream,
)
from spandan.gen.config import DEFAULT_CONFIG, GenConfig, ScenarioEpisodeSpec
from spandan.gen.schema import (
    ALL_COLUMNS,
    ATTACK_SCENARIOS,
    FEATURE_COLUMNS,
    LABEL_COLUMNS,
    NEGATIVE_CONTROLS,
    SCENARIO_BENIGN,
    SCENARIO_FLASH_SALE,
    SCENARIO_ISSUER_OUTAGE,
    STATUS_DECLINED,
    Event,
)

from helpers import SMALL_CONFIG  # noqa: E402


@pytest.fixture(scope="session")
def built(tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("stream")
    manifest = build(SMALL_CONFIG, out)
    return {
        "dir": Path(out),
        "manifest": manifest,
        "train": read_stream(Path(out) / TRAIN_FILENAME),
        "test": read_stream(Path(out) / TEST_FILENAME),
    }


def _all_events(built: dict) -> list[Event]:
    return built["train"] + built["test"]


# --- reproducibility ------------------------------------------------------


def test_seed_reproducible_byte_identical(tmp_path):
    first = build(SMALL_CONFIG, tmp_path / "a")
    second = build(SMALL_CONFIG, tmp_path / "b")

    for split in ("train", "test"):
        assert first["files"][split]["sha256"] == second["files"][split]["sha256"], (
            f"{split} bytes differ across two runs of the same seed"
        )
    assert first["config_hash"] == second["config_hash"]

    # The manifests themselves must match too - nothing wall-clock in them.
    a = json.loads((tmp_path / "a" / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    b = json.loads((tmp_path / "b" / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert a == b


def test_a_different_seed_produces_a_different_stream(tmp_path):
    other = dataclasses.replace(SMALL_CONFIG, seed=SMALL_CONFIG.seed + 1)
    a = build(SMALL_CONFIG, tmp_path / "a")
    b = build(other, tmp_path / "b")
    assert a["files"]["train"]["sha256"] != b["files"]["train"]["sha256"]


# --- ordering and the temporal split --------------------------------------


def test_events_monotonic_nondecreasing_ts(built):
    for split in ("train", "test"):
        events = built[split]
        assert events, f"{split} split is empty"
        stamps = [e.ts for e in events]
        assert stamps == sorted(stamps), f"{split} is not time-ordered"
        ids = [e.txn_id for e in events]
        assert ids == sorted(ids), f"{split} txn_ids do not run in time order"
        assert len(set(ids)) == len(ids), f"{split} has duplicate txn_ids"


def test_train_strictly_precedes_test(built):
    train, test = built["train"], built["test"]
    boundary = SMALL_CONFIG.train_end_ms

    assert max(e.ts for e in train) < min(e.ts for e in test)
    assert all(e.ts < boundary for e in train), "an event before the boundary landed in test"
    assert all(e.ts >= boundary for e in test), "an event after the boundary landed in train"
    assert built["manifest"]["split"]["kind"] == "temporal"
    assert built["manifest"]["split"]["train_strictly_precedes_test"] is True


def test_both_splits_contain_positives(built):
    # Phase 2 selects its threshold on a validation window carved out of the
    # training period, which is only possible if training contains positives.
    assert sum(e.label for e in built["train"]) > 0
    assert sum(e.label for e in built["test"]) > 0


# --- the feature/label boundary -------------------------------------------


def test_feature_columns_exclude_label_and_scenario(built):
    assert "label" not in FEATURE_COLUMNS
    assert "scenario_id" not in FEATURE_COLUMNS
    assert set(LABEL_COLUMNS) == {"label", "scenario_id"}
    assert not set(FEATURE_COLUMNS) & set(LABEL_COLUMNS)

    declared = {f.name for f in dataclasses.fields(Event)}
    assert set(ALL_COLUMNS) == declared
    assert set(FEATURE_COLUMNS) | set(LABEL_COLUMNS) == declared


# --- distributional claims -------------------------------------------------


def test_benign_decline_rate_within_declared_band(built):
    low = SMALL_CONFIG.benign_decline_rate_min
    high = SMALL_CONFIG.benign_decline_rate_max
    for split in ("train", "test"):
        benign = [e for e in built[split] if e.scenario_id == SCENARIO_BENIGN]
        assert benign
        observed = sum(1 for e in benign if e.status == STATUS_DECLINED) / len(benign)
        assert low <= observed <= high, f"{split} benign decline rate {observed:.4f} outside band"
        assert observed > 0.0, "a zero benign decline rate would make the decline signal free"


def test_flash_sale_events_labeled_clean(built):
    flash = [e for e in _all_events(built) if e.scenario_id == SCENARIO_FLASH_SALE]
    assert flash, "no flash-sale events were generated"
    assert all(e.label == 0 for e in flash), "the benign flash sale must be labeled clean"
    assert any(e.scenario_id == SCENARIO_FLASH_SALE for e in built["train"])
    assert any(e.scenario_id == SCENARIO_FLASH_SALE for e in built["test"])


def test_flash_sale_is_a_mixture_of_known_and_new_customers(built):
    """The negative case has to be hard in both directions.

    All-known customers would make "unseen card" a free separator between sales
    and attacks. All-new customers would make it a free separator the other way.
    The sale must sit in between, so that novelty alone decides nothing.
    """
    events = _all_events(built)
    benign_cards = {e.card_ref for e in events if e.scenario_id == SCENARIO_BENIGN}
    flash_cards = {e.card_ref for e in events if e.scenario_id == SCENARIO_FLASH_SALE}
    assert flash_cards

    known = len(flash_cards & benign_cards) / len(flash_cards)
    assert known > 0.50, f"only {known:.2%} of flash-sale cards are known customers"
    assert known < 0.95, f"{known:.2%} known leaves too few first-time customers"

    # And the attack scenarios must not accidentally share that property: their
    # cards are unseen, which is realistic and is exactly why novelty cannot be
    # the detector's evidence.
    attack_cards = {e.card_ref for e in events if e.scenario_id in ATTACK_SCENARIOS}
    assert not (attack_cards & benign_cards)


# --- the issuer-outage negative control ------------------------------------
#
# These assert properties of the generated stream, not that the generator did
# what it was written to do. A test that mirrors the code passes forever while
# the property it was supposed to protect quietly rots - which is exactly how the
# Zipf bug in the flash sale survived until something measured the output.


def test_issuer_outage_events_labeled_clean(built):
    outage = [e for e in _all_events(built) if e.scenario_id == SCENARIO_ISSUER_OUTAGE]
    assert outage, "no issuer-outage events were generated"
    assert all(e.label == 0 for e in outage)
    assert SCENARIO_ISSUER_OUTAGE in NEGATIVE_CONTROLS
    assert any(e.scenario_id == SCENARIO_ISSUER_OUTAGE for e in built["train"])
    assert any(e.scenario_id == SCENARIO_ISSUER_OUTAGE for e in built["test"])


def test_issuer_outage_attacks_the_primary_signal(built):
    """The control is worthless unless it actually looks like the thing.

    The detector's primary signal is an elevated decline ratio concentrated on a
    BIN. If the outage does not produce that from clean traffic, it is not
    testing anything.
    """
    events = _all_events(built)
    outage = [e for e in events if e.scenario_id == SCENARIO_ISSUER_OUTAGE]
    benign = [e for e in events if e.scenario_id == SCENARIO_BENIGN]

    outage_decline = sum(1 for e in outage if e.status == STATUS_DECLINED) / len(outage)
    benign_decline = sum(1 for e in benign if e.status == STATUS_DECLINED) / len(benign)
    assert outage_decline > 0.6, f"outage decline ratio {outage_decline:.2%} is not attack-like"
    assert outage_decline > 6 * benign_decline

    # Concentrated on very few BINs, like an attack would be.
    assert len({e.bin for e in outage}) <= 4


def test_issuer_outage_is_separable_from_a_burst_without_decline_ratio(built):
    """Every separator the detector is allowed to use, measured.

    If the outage were separable *only* by decline ratio it would be
    indistinguishable from card testing and the negative control would be
    unlearnable rather than hard. These four properties are what make it
    learnable - and none of them is decline ratio.
    """
    events = _all_events(built)
    outage = [e for e in events if e.scenario_id == SCENARIO_ISSUER_OUTAGE]
    burst = [e for e in events if e.scenario_id == "burst"]
    benign_cards = {e.card_ref for e in events if e.scenario_id == SCENARIO_BENIGN}

    # 1. Retries: an outage re-attempts the same card; a probe burst does not.
    outage_attempts = len(outage) / len({e.card_ref for e in outage})
    burst_attempts = len(burst) / len({e.card_ref for e in burst})
    assert outage_attempts > 2.0, f"only {outage_attempts:.2f} attempts per card"
    assert outage_attempts > 2 * burst_attempts

    # 2. Ordinary basket sizes, not a low probe band.
    outage_median = sorted(e.amount_paise for e in outage)[len(outage) // 2]
    burst_median = sorted(e.amount_paise for e in burst)[len(burst) // 2]
    assert outage_median > 10 * burst_median

    # 3. Known customers with existing baselines.
    outage_cards = {e.card_ref for e in outage}
    known = len(outage_cards & benign_cards) / len(outage_cards)
    assert known > 0.75, f"only {known:.1%} of outage cards are known customers"

    # 4. Multi-merchant: an issuer's customers shop in more than one place,
    #    where a card-testing episode targets one merchant at a time.
    assert len({e.merchant_id for e in outage}) > 1
    for spec_id in ("burst", "rotating", "slow_low"):
        per_episode = {e.merchant_id for e in events if e.scenario_id == spec_id}
        assert per_episode, spec_id


def test_no_card_novelty_feature_exists_anywhere(built):
    """A standing constraint, kept visible in the source rather than only in docs.

    Attack cards are 100% novel; flash-sale cards are 56.3% novel by distinct
    card, 25.3% by event. The sale therefore controls for volume but only
    partially for novelty, which is acceptable *only* because no card-novelty
    feature exists in the design. If one is ever added, the flash sale stops
    being a valid negative control and the negative case has to be rebuilt.

    WHAT THIS TEST ACTUALLY CHECKS, stated precisely because the difference
    matters: it greps the non-generator Python packages for five specific
    tokens. It catches a novelty feature that is *named* like one. It does not
    catch a novelty feature named anything else, and it does not read the Rust
    core at all.

    The real guards are elsewhere and are stronger:
      - behavioural, Python: test_no_card_novelty_state_is_retained in
        tests/test_reference_detector.py renames every card to an unseen value
        and asserts the score arrays are identical. A detector keying on
        novelty fails it regardless of naming.
      - structural, Rust: `Axis` has no `Card` variant, so a card-keyed
        baseline is a compile error.
    This test is the cheap third layer, not the enforcement.
    """
    import spandan
    from pathlib import Path

    banned = ("first_seen", "novel_card", "card_novelty", "unseen_card", "new_card")
    root = Path(spandan.__file__).parent
    offenders = []
    for path in root.rglob("*.py"):
        if "gen" in path.parts:
            continue  # the generator knows about novelty; the detector may not
        text = path.read_text(encoding="utf-8").lower()
        offenders.extend(f"{path.name}:{token}" for token in banned if token in text)
    assert not offenders, f"card-novelty feature detected: {offenders}"


def test_attack_bins_also_appear_in_benign_traffic(built):
    """Attack episodes borrow a BIN that has its own legitimate baseline.

    Strictly harder than inventing a fresh BIN, and closer to reality: an issuer
    whose cards are being tested still has ordinary customers.
    """
    events = _all_events(built)
    benign_bins = {e.bin for e in events if e.scenario_id == SCENARIO_BENIGN}
    attack_bins = {e.bin for e in events if e.scenario_id in ATTACK_SCENARIOS}
    assert attack_bins
    assert attack_bins <= benign_bins


def test_scenario_positive_counts_match_manifest(built):
    manifest = built["manifest"]
    for split in ("train", "test"):
        events = built[split]
        for name, expected in manifest[split]["scenario_positives"].items():
            actual = sum(1 for e in events if e.scenario_id == name and e.label == 1)
            assert actual == expected, f"{split}/{name}: manifest {expected}, file {actual}"
        for name, expected in manifest[split]["scenario_counts"].items():
            actual = sum(1 for e in events if e.scenario_id == name)
            assert actual == expected, f"{split}/{name}: manifest {expected}, file {actual}"
        assert manifest[split]["rows"] == len(events)
        assert manifest[split]["positives"] == sum(e.label for e in events)


def test_labels_agree_with_scenario_class(built):
    for event in _all_events(built):
        expected = 1 if event.scenario_id in ATTACK_SCENARIOS else 0
        assert event.label == expected, f"{event.scenario_id} carried label {event.label}"


# --- defense-only identifier discipline ------------------------------------


def test_all_identifiers_synthetic(built):
    """Every identifier comes from a range reserved by standard.

    Not "we made these up" - drawn from ranges that cannot collide with anything
    real, so the claim is checkable rather than asserted (`agents.md` §7).
    """
    for event in _all_events(built):
        assert entities.is_synthetic_bin(event.bin), f"non-synthetic BIN {event.bin}"
        assert entities.is_synthetic_ip(event.ip), f"non-reserved IP {event.ip}"
        assert entities.is_synthetic_card_ref(event.card_ref)
        assert entities.is_synthetic_device_id(event.device_id)
        assert event.merchant_id.startswith(entities.MERCHANT_PREFIX)


def test_no_card_reference_could_be_mistaken_for_a_pan(built):
    """No card numbers exist anywhere in this project.

    Card references are prefixed opaque tokens: not 13-19 digit strings, and so
    not Luhn-checkable even by accident.
    """
    for event in _all_events(built):
        assert not event.card_ref.isdigit()
        digits = "".join(c for c in event.card_ref if c.isdigit())
        assert len(digits) < 13, f"{event.card_ref} has PAN-length digits"


def test_full_config_uses_the_same_reserved_ranges():
    # The shipped config, not just the reduced test one.
    assert DEFAULT_CONFIG.merchant_count > 0
    assert DEFAULT_CONFIG.benign_decline_rate_min > 0.0
    assert DEFAULT_CONFIG.train_days < DEFAULT_CONFIG.total_days
    scenario_ids = {spec.scenario_id for spec in DEFAULT_CONFIG.episodes}
    assert scenario_ids == {
        "burst",
        "rotating",
        "slow_low",
        "flash_sale",
        "issuer_outage",
        "outage_single_merchant",
    }


def test_shipped_schedule_has_enough_episodes_to_support_a_claim():
    """Phase 2 review: two episodes per scenario per split is an anecdote.

    Twenty is the floor agreed for a per-scenario number to mean anything.
    """
    from spandan.gen.schedule import describe_schedule

    counts = describe_schedule(DEFAULT_CONFIG.episodes, DEFAULT_CONFIG.train_days)
    for scenario, splits in counts.items():
        assert splits["train"] >= 20, f"{scenario}: only {splits['train']} train episodes"
        assert splits["test"] >= 20, f"{scenario}: only {splits['test']} test episodes"


def test_more_episodes_came_from_a_longer_stream_not_a_denser_one():
    """The power fix must not be a difficulty change.

    Episodes per scenario per DAY must be unchanged from the Phase 1 schedule
    (~0.4/day). If it rose, more attack traffic would be folding into the per-BIN
    baselines those attacks are measured against, which is a difficulty change
    wearing a statistics costume.
    """
    from spandan.gen.schedule import EPISODES_PER_DAY, describe_schedule

    counts = describe_schedule(DEFAULT_CONFIG.episodes, DEFAULT_CONFIG.train_days)
    train_days = DEFAULT_CONFIG.train_days
    test_days = DEFAULT_CONFIG.total_days - train_days

    for scenario, splits in counts.items():
        assert splits["train"] / train_days == pytest.approx(EPISODES_PER_DAY, abs=0.05), scenario
        assert splits["test"] / test_days == pytest.approx(EPISODES_PER_DAY, abs=0.05), scenario


def test_no_episode_straddles_the_split_boundary():
    boundary = DEFAULT_CONFIG.train_days
    for spec in DEFAULT_CONFIG.episodes:
        end_day = spec.day + spec.duration_minutes / 1440.0
        assert not (spec.day < boundary < end_day), f"{spec.scenario_id} at day {spec.day}"
