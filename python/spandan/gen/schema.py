"""The transaction event record, and the frozen column lists built on it.

One schema serves the whole project: the generator writes it, the detector reads
it, and the eval harness scores it. The split between `FEATURE_COLUMNS` and
`LABEL_COLUMNS` is the thing that keeps them honest — a detector that can see
`label` or `scenario_id` is not a detector, and Phase 1 freezes that boundary
before any scoring code exists to blur it.

Timestamps are integer epoch milliseconds (UTC). Integers, not floats, because
byte-identical regeneration under a fixed seed is an acceptance criterion and
float formatting is not something to have to trust.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

# --- status ---------------------------------------------------------------

STATUS_APPROVED = "approved"
STATUS_DECLINED = "declined"
STATUSES = (STATUS_APPROVED, STATUS_DECLINED)

# --- scenarios ------------------------------------------------------------
#
# Scenario ids are evaluation bookkeeping, not detector input. They exist so the
# eval harness can report recall per scenario and time-to-detection per scenario
# rather than one undifferentiated number.

SCENARIO_BENIGN = "benign"
SCENARIO_BURST = "burst"
SCENARIO_ROTATING = "rotating"
SCENARIO_SLOW_LOW = "slow_low"
SCENARIO_FLASH_SALE = "flash_sale"

#: Scenarios whose events carry label 1.
ATTACK_SCENARIOS = (SCENARIO_BURST, SCENARIO_ROTATING, SCENARIO_SLOW_LOW)

#: Scenarios whose events carry label 0. `flash_sale` is the important one: it is
#: a volume surge that looks like an attack on every volume-shaped feature and is
#: labeled clean. It is the false-positive test the whole cost model rests on.
CLEAN_SCENARIOS = (SCENARIO_BENIGN, SCENARIO_FLASH_SALE)

SCENARIOS = CLEAN_SCENARIOS + ATTACK_SCENARIOS


@dataclass(frozen=True, slots=True)
class Event:
    """One transaction attempt.

    Frozen: nothing downstream may relabel an event in place.
    """

    ts: int
    """Epoch milliseconds, UTC."""

    txn_id: str
    merchant_id: str

    bin: str
    """Six-digit synthetic BIN. See `entities.SYNTHETIC_BIN_MII` — every value
    here begins with a digit that no card scheme is issued, so no synthetic BIN
    can collide with a real issuer."""

    card_ref: str
    """Opaque synthetic reference for a card. **Not a PAN**, not derived from one,
    and not Luhn-valid: no card number is generated anywhere in this project."""

    ip: str
    """IPv4 drawn only from reserved documentation/benchmarking ranges."""

    device_id: str
    amount_paise: int
    status: str

    label: int
    """1 = card-testing activity, 0 = clean. Ground truth; never a model input."""

    scenario_id: str
    """Which generator population produced this event. Evaluation only."""


#: What a detector is allowed to see.
FEATURE_COLUMNS = (
    "ts",
    "txn_id",
    "merchant_id",
    "bin",
    "card_ref",
    "ip",
    "device_id",
    "amount_paise",
    "status",
)

#: What only the evaluation harness may see.
LABEL_COLUMNS = ("label", "scenario_id")

#: Serialization order. Stable, because the output files are hashed.
ALL_COLUMNS = FEATURE_COLUMNS + LABEL_COLUMNS


def _check_columns_partition_the_schema() -> None:
    declared = tuple(f.name for f in fields(Event))
    if set(ALL_COLUMNS) != set(declared):
        missing = set(declared) - set(ALL_COLUMNS)
        extra = set(ALL_COLUMNS) - set(declared)
        raise AssertionError(
            f"column lists do not partition Event: missing={sorted(missing)} "
            f"extra={sorted(extra)}"
        )
    if set(FEATURE_COLUMNS) & set(LABEL_COLUMNS):
        raise AssertionError("FEATURE_COLUMNS and LABEL_COLUMNS overlap")


_check_columns_partition_the_schema()


def to_record(event: Event) -> dict:
    """Serializable dict in `ALL_COLUMNS` order."""
    return {name: getattr(event, name) for name in ALL_COLUMNS}


def from_record(record: dict) -> Event:
    return Event(**{name: record[name] for name in ALL_COLUMNS})
