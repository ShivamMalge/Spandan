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

import numbers
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
SCENARIO_ISSUER_OUTAGE = "issuer_outage"
SCENARIO_OUTAGE_SINGLE = "outage_single_merchant"

#: Scenarios whose events carry label 1.
ATTACK_SCENARIOS = (SCENARIO_BURST, SCENARIO_ROTATING, SCENARIO_SLOW_LOW)

#: Scenarios whose events carry label 0. Two of them are deliberate negative
#: controls, each attacking a different one of the detector's axes:
#:
#: - `flash_sale` attacks the **volume** axis: a genuine surge that looks like an
#:   attack on every volume-shaped feature.
#: - `issuer_outage` attacks the **decline-ratio** axis, which is the detector's
#:   primary signal: entirely legitimate traffic on one BIN declining at
#:   card-testing rates because the issuer is down, with customers retrying.
#:
#: A detector that flags either is expensive to run, and the rupee cost model
#: reports each separately.
CLEAN_SCENARIOS = (
    SCENARIO_BENIGN,
    SCENARIO_FLASH_SALE,
    SCENARIO_ISSUER_OUTAGE,
    SCENARIO_OUTAGE_SINGLE,
)

#: The labeled-clean scenarios that exist specifically to be hard.
NEGATIVE_CONTROLS = (
    SCENARIO_FLASH_SALE,
    SCENARIO_ISSUER_OUTAGE,
    SCENARIO_OUTAGE_SINGLE,
)

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

    def __post_init__(self) -> None:
        # The input contract, enforced once at the boundary so both engines see
        # the same events. Before this the Python reference counted any status
        # that was not "declined" as approved - a real "failed" would have
        # zeroed the primary signal - while the Rust core raised on it
        # (external audit, 2026-09-03, B4). Cheap checks only: this runs on
        # every one of 1.6M events at load.
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}; expected one of {STATUSES}")
        # txn_id is type-checked only: the generator builds events with an empty
        # id and assigns ids after the total-order sort (build.generate_events).
        if not isinstance(self.txn_id, str):
            raise TypeError(f"txn_id must be a str, got {self.txn_id!r}")
        for name in ("merchant_id", "bin", "card_ref", "ip", "device_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise TypeError(f"{name} must be a non-empty str, got {value!r}")
        # numbers.Integral, not int: the generator hands over numpy integers.
        if not isinstance(self.ts, numbers.Integral) or isinstance(self.ts, bool):
            raise TypeError(f"ts must be an int of epoch milliseconds, got {self.ts!r}")
        if (not isinstance(self.amount_paise, numbers.Integral) or isinstance(self.amount_paise, bool)
                or self.amount_paise < 0):
            raise ValueError(f"amount_paise must be a non-negative int, got {self.amount_paise!r}")


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
