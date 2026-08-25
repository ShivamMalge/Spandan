"""The detector seam.

Everything downstream of this file talks to a `Detector`, never to a concrete
implementation. Three things depend on that:

1. **Phase 4** swaps the Rust core in behind this exact interface, so `make eval`
   produces the same numbers through either engine and the difference is a
   benchmark rather than a rewrite.
2. **Phase 3** tests the Rust core for numerical parity against the Python
   implementation of this interface, so this module is the written spec.
3. **Phase 5** hands `Flag` objects to the LLM layer. `Flag` is frozen and the
   explanation path returns a string, so there is no code path by which an LLM
   can alter a score, a threshold, or a label.

Window-boundary convention, fixed here because Phase 3's parity work depends on
it being written down rather than inferred:

    A sliding window of width W at time t covers the half-open interval
    (t - W, t]. The current event is always inside its own window. An event
    exactly W milliseconds old has fallen out.

Ties: events with identical timestamps are processed in stream order, and each
sees every earlier-or-equal event including itself.

## WHAT A FLAG DOES

**A flag declines the transaction it is raised on. It is an inline authorization
control, not a notification.** Alerts are the human-facing aggregation of those
declines, grouped per (merchant, BIN); they are how an analyst reviews what the
control did, not a separate action.

This was ambiguous through Phase 2 and the ambiguity was doing real damage,
because the report had it both ways: it constrained the operating point on
*alerts per day* — a measure of analyst workload — while the cost model charged
blocked-good-transaction value *per event*, which only makes sense if flags
block. Stated one way, capping alerts reads as capping merchant impact. It is
not, and the two are not even close: at the headline operating point 20,254
flagged events collapse into 487 alerts, so an alert budget bounds the review
queue and says nothing about how many customers were declined.

Consequences of choosing "flags block", all of which the evaluation must carry:

- **The event flag rate is the real operational constraint**, and it belongs
  beside alerts/day everywhere either appears.
- The sharper form is the **legitimate-transaction decline rate**: false
  positives divided by clean events. That is the number a merchant feels, and
  the one that decides deployability.
- The cost model's blocked-good term is correct as written, and so is the
  avoided-chargeback term: both assume the attempt was stopped.
- A detector that declines 1 in 40 transactions is not deployable at *any* alert
  budget. See `docs/FAILURE_MODES.md`.

The alternative reading — flags only notify — was available and is equally
defensible in the abstract, but it would invalidate both sides of the rupee
model: nothing is prevented until a human acts, so neither the blocked-good cost
nor the avoided-chargeback saving could be claimed without a response-time model
this project does not have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np

from ..gen.schema import Event

#: The entity axes the detector keys on.
#:
#: Card is deliberately absent. Per `gen/ASSUMPTIONS.md` §1.7a no feature may be
#: derived from card novelty or first-seen-ness, because the flash-sale control
#: only partially controls for novelty. Cards are used within a window to measure
#: *repetition*, which is a different thing and needs no history.
AXES = ("bin", "ip", "device", "merchant")


@dataclass(frozen=True, slots=True)
class DetectorConfig:
    """Every tunable, in one frozen place.

    All of these are chosen on a validation window carved from the training
    period. None is chosen on the test set (`agents.md` §6).
    """

    window_ms: int = 300_000
    """Sliding window width. 5 minutes."""

    ring_capacity: int = 512
    """Per-entity ring buffer size. Bounds memory: the detector cannot grow
    without limit no matter how hot an entity gets. An entity exceeding this
    within one window has its oldest retained events dropped, which is recorded
    rather than hidden — see `Flag.window_saturated`."""

    baseline_sample_interval_ms: int = 60_000
    """Minimum gap between folding an entity's window count into its baseline.

    Without this, a burst would fold hundreds of inflated samples into the very
    baseline it is being measured against and would partly hide itself."""

    baseline_min_samples: int = 20
    """An entity scores 0 until its baseline has this many samples. Cold entities
    are not evidence; they are just cold."""

    ewma_halflife_samples: float = 30.0

    # --- scoring weights -------------------------------------------------
    w_velocity_bin: float = 1.0
    w_decline_bin: float = 2.0
    w_amount: float = 1.0
    w_velocity_ip: float = 0.5
    w_repetition_damping: float = 1.2
    w_merchant_span_damping: float = 1.4

    threshold: float = 3.0
    """Chosen on the validation window. The default here is a placeholder that
    `make eval` overwrites with the selected value."""

    # --- ablation switches ----------------------------------------------
    use_ewma: bool = True
    """Off = compare against a fixed global mean instead of a per-entity
    baseline. The drop-EWMA ablation."""

    use_per_ip: bool = True
    """Off = drop the per-IP axis entirely. The drop-per-IP ablation."""

    def with_(self, **changes) -> "DetectorConfig":
        from dataclasses import replace

        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class Flag:
    """A scored event, above threshold.

    Frozen. The evidence fields exist so that Phase 5 can explain a flag without
    recomputing anything and without touching the detector.
    """

    ts: int
    txn_id: str
    merchant_id: str
    bin: str
    score: float
    threshold: float

    window_events: int
    window_declines: int
    window_decline_ratio: float
    baseline_decline_ratio: float
    velocity_z: float
    baseline_window_events: float
    window_distinct_cards: int
    cards_per_event: float
    window_distinct_merchants: int
    window_amount_mean_paise: float
    baseline_amount_mean_paise: float
    window_saturated: bool

    contributions: tuple[tuple[str, float], ...] = field(default=())
    """(term, signed contribution) in score units. The terms sum to `score`, so
    an explanation can never assert a cause the arithmetic does not support."""


@runtime_checkable
class Detector(Protocol):
    """Streaming and batch surfaces over the same state machine."""

    def update(self, event: Event) -> Flag | None:
        """Feed one event. Returns a Flag if it scored above threshold."""
        ...

    def score_batch(self, events: list[Event]) -> np.ndarray:
        """Score a whole stream, returning one float per event, in order.

        Must be identical to feeding the same events through `update` one at a
        time — asserted by `test_streaming_matches_batch`.
        """
        ...

    def reset(self) -> None:
        ...
