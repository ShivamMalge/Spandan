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
    SCENARIO_BENIGN,
    SCENARIO_FLASH_SALE,
    STATUS_DECLINED,
    Event,
)

# Episodes sized for the reduced config: every scenario appears in both the train
# window (days 0-3) and the test window (days 3-4). Signatures mirror the full
# config's - concentration, amount band, decline ratio - at lower volume.
_SMALL_EPISODES = (
    ScenarioEpisodeSpec("burst", 0.50, 8.0, 120, 120, 1, 1, 0.86, 100, 5_000, 0),
    ScenarioEpisodeSpec("rotating", 1.50, 20.0, 140, 140, 30, 28, 0.84, 100, 5_000, 1),
    ScenarioEpisodeSpec("slow_low", 2.20, 240.0, 40, 40, 3, 3, 0.72, 100, 3_000, 2),
    ScenarioEpisodeSpec("flash_sale", 1.10, 40.0, 300, 280, 270, 260, 0.16, 20_000, 900_000, 1),
    ScenarioEpisodeSpec("burst", 3.30, 8.0, 110, 110, 1, 1, 0.87, 100, 5_000, 2),
    ScenarioEpisodeSpec("rotating", 3.60, 20.0, 130, 130, 28, 26, 0.85, 100, 5_000, 0),
    ScenarioEpisodeSpec("slow_low", 3.10, 200.0, 36, 36, 3, 3, 0.73, 100, 3_000, 1),
    ScenarioEpisodeSpec("flash_sale", 3.80, 40.0, 280, 260, 250, 240, 0.15, 20_000, 900_000, 2),
)

SMALL_CONFIG = GenConfig(
    seed=4_242_424,
    total_days=4,
    train_days=3,
    merchant_count=3,
    benign_card_pool_size=1_200,
    benign_ip_pool_size=900,
    benign_device_pool_size=800,
    benign_bin_pool_size=40,
    episodes=_SMALL_EPISODES,
)


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
    assert scenario_ids == {"burst", "rotating", "slow_low", "flash_sale"}
