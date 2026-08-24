"""Reference implementation of the detector. This module is the spec.

Five stages, matching the five Rust modules Phase 3 will write:

    ingest    -> the typed event (already `gen.schema.Event`)
    state     -> per-entity store, keyed by (axis, value)
    velocity  -> fixed-capacity ring buffers, sliding-window counts and declines
    baseline  -> EWMA + Welford per entity, fed on a sample gate
    score     -> evidence terms minus damping terms

Deliberately scalar and loop-shaped rather than vectorised. It has to be readable
as a specification, and Phase 4 needs it as an honest NumPy-idiomatic baseline
rather than a version tuned to lose.

## What the score is, and is not

It is a **deviation from a per-entity baseline**, not a probability. That is why
Phase 2 reports a cost-vs-threshold sweep rather than a calibration curve: asking
whether 0.7 "means" 70% would be asking a question the quantity cannot answer.

## Evidence and damping

Positive evidence that this looks like card testing:

- `velocity_bin`   — window event count on this BIN, in standard deviations above
                     that BIN's own baseline.
- `decline_bin`    — window decline ratio above this BIN's baseline decline ratio.
                     The primary signal.
- `amount`         — how far below baseline the window's mean amount sits.
                     Probes are small; ordinary baskets are not.
- `velocity_ip`    — the same velocity term on the IP axis. Catches a
                     single-address burst, and by construction misses a rotating
                     one, which is what makes the drop-per-IP ablation informative.

Damping, subtracted, because these are what an issuer outage looks like and card
testing does not:

- `repetition`     — events per distinct card **inside the current window**.
                     Retries mean the same card again; a probe run does not
                     revisit a dead card.
- `merchant_span`  — distinct merchants for this BIN inside the window. An
                     issuer's customers shop in several places at once; a
                     card-testing episode is at one merchant.

`repetition` deserves a note, because it is the closest thing here to a banned
feature. `gen/ASSUMPTIONS.md` §1.7a forbids any feature derived from card novelty
or first-seen-ness. This is not one: it is computed only from the cards present in
the current window and never consults whether a card was seen before, so it would
behave identically on a stream where every card had appeared a thousand times
already. It measures retry behaviour, not novelty.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np

from ..gen.schema import STATUS_DECLINED, Event
from .interface import Detector, DetectorConfig, Flag


class Welford:
    """Streaming mean and variance. Numerically stable, and the thing Phase 3
    property-tests against a naive two-pass computation."""

    __slots__ = ("count", "mean", "m2")

    def __init__(self) -> None:
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0

    def update(self, value: float) -> None:
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (value - self.mean)

    @property
    def variance(self) -> float:
        return self.m2 / (self.count - 1) if self.count > 1 else 0.0

    @property
    def stddev(self) -> float:
        return math.sqrt(self.variance)


class Ewma:
    """Exponentially weighted mean over samples (not over time)."""

    __slots__ = ("alpha", "value", "count")

    def __init__(self, halflife_samples: float) -> None:
        self.alpha = 1.0 - math.exp(-math.log(2.0) / max(halflife_samples, 1e-9))
        self.value = 0.0
        self.count = 0

    def update(self, sample: float) -> None:
        if self.count == 0:
            self.value = sample
        else:
            self.value += self.alpha * (sample - self.value)
        self.count += 1


class EntityState:
    """Per-entity sliding window and baselines.

    The ring buffer is fixed-capacity, so memory is bounded per entity no matter
    how hot it gets. That bound is the property Phase 3 property-tests, and it is
    the reason this design can claim bounded-memory streaming rather than merely
    fast batch scoring.
    """

    __slots__ = (
        "events",
        "declines_in_window",
        "amount_sum",
        "baseline_count",
        "baseline_declines",
        "baseline_amount",
        "ewma_count",
        "last_baseline_ms",
        "saturated",
    )

    def __init__(self, capacity: int, halflife: float) -> None:
        self.events: deque = deque(maxlen=capacity)
        self.declines_in_window = 0
        self.amount_sum = 0.0
        self.baseline_count = Welford()
        self.baseline_declines = Welford()
        self.baseline_amount = Welford()
        self.ewma_count = Ewma(halflife)
        self.last_baseline_ms = -(1 << 62)
        self.saturated = False

    def push(self, ts: int, declined: bool, amount: float, card: str, merchant: str) -> None:
        if len(self.events) == self.events.maxlen:
            # Ring is full: the oldest retained event is about to be evicted by
            # the deque itself. Account for it, and record that this entity hit
            # its capacity bound rather than pretending the window is exact.
            old = self.events[0]
            self.declines_in_window -= int(old[1])
            self.amount_sum -= old[2]
            self.saturated = True
        self.events.append((ts, declined, amount, card, merchant))
        self.declines_in_window += int(declined)
        self.amount_sum += amount

    def evict_before(self, cutoff_ms: int) -> None:
        """Drop everything at or before `cutoff`. Window is (t - W, t]."""
        while self.events and self.events[0][0] <= cutoff_ms:
            _, declined, amount, _, _ = self.events.popleft()
            self.declines_in_window -= int(declined)
            self.amount_sum -= amount

    def fold_baseline(self, ts: int, interval_ms: int) -> None:
        """Fold the current window into the baseline, at most once per interval.

        The gate is load-bearing. Folding on every event would let a burst pour
        hundreds of inflated samples into the baseline it is being compared
        against, and it would partly conceal itself.
        """
        if ts - self.last_baseline_ms < interval_ms:
            return
        self.last_baseline_ms = ts
        count = len(self.events)
        self.baseline_count.update(float(count))
        self.ewma_count.update(float(count))
        if count:
            self.baseline_declines.update(self.declines_in_window / count)
            self.baseline_amount.update(self.amount_sum / count)


class ReferenceDetector(Detector):
    def __init__(self, config: DetectorConfig | None = None) -> None:
        self.config = config or DetectorConfig()
        self._state: dict[tuple[str, str], EntityState] = {}
        self._global_count = Welford()

    def reset(self) -> None:
        self._state.clear()
        self._global_count = Welford()

    # --- state ------------------------------------------------------------

    def _entity(self, axis: str, value: str) -> EntityState:
        key = (axis, value)
        state = self._state.get(key)
        if state is None:
            state = EntityState(self.config.ring_capacity, self.config.ewma_halflife_samples)
            self._state[key] = state
        return state

    # --- scoring ----------------------------------------------------------

    def _velocity_z(self, state: EntityState) -> float:
        """Window count in standard deviations above this entity's baseline.

        With `use_ewma` off, the comparison is against a single global mean
        instead of a per-entity baseline — that is the drop-EWMA ablation, and it
        is what a detector without per-entity memory would have to do.
        """
        count = float(len(state.events))
        if self.config.use_ewma:
            if state.baseline_count.count < self.config.baseline_min_samples:
                return 0.0
            centre = state.ewma_count.value
            spread = state.baseline_count.stddev
        else:
            if self._global_count.count < self.config.baseline_min_samples:
                return 0.0
            centre = self._global_count.mean
            spread = self._global_count.stddev
        if spread <= 1e-9:
            return 0.0
        return (count - centre) / spread

    def _score(self, event: Event, axis_states: dict[str, EntityState]) -> tuple[float, dict]:
        cfg = self.config
        bin_state = axis_states["bin"]
        window = list(bin_state.events)
        count = len(window)

        decline_ratio = bin_state.declines_in_window / count if count else 0.0
        baseline_decline = (
            bin_state.baseline_declines.mean
            if bin_state.baseline_declines.count >= cfg.baseline_min_samples
            else 0.0
        )
        amount_mean = bin_state.amount_sum / count if count else 0.0
        baseline_amount = (
            bin_state.baseline_amount.mean
            if bin_state.baseline_amount.count >= cfg.baseline_min_samples
            else 0.0
        )

        distinct_cards = len({row[3] for row in window})
        distinct_merchants = len({row[4] for row in window})
        cards_per_event = distinct_cards / count if count else 1.0

        warm = bin_state.baseline_count.count >= cfg.baseline_min_samples or not cfg.use_ewma

        velocity_bin = max(0.0, self._velocity_z(bin_state))
        decline_excess = max(0.0, decline_ratio - baseline_decline) if warm else 0.0

        if warm and baseline_amount > 0.0 and amount_mean > 0.0:
            amount_term = max(0.0, math.log(baseline_amount / amount_mean))
        else:
            amount_term = 0.0

        velocity_ip = 0.0
        if cfg.use_per_ip:
            velocity_ip = max(0.0, self._velocity_z(axis_states["ip"]))

        # Damping. `repetition` rises when the same card is retried inside the
        # window; `span` rises when one BIN is active at many merchants at once.
        repetition = max(0.0, 1.0 / max(cards_per_event, 1e-9) - 1.0) if count else 0.0
        span = max(0.0, float(distinct_merchants) - 1.0)

        terms = {
            "velocity_bin": cfg.w_velocity_bin * velocity_bin,
            "decline_bin": cfg.w_decline_bin * decline_excess * 10.0,
            "amount": cfg.w_amount * amount_term,
            "velocity_ip": cfg.w_velocity_ip * velocity_ip,
            "repetition": -cfg.w_repetition_damping * repetition,
            "merchant_span": -cfg.w_merchant_span_damping * span,
        }
        score = sum(terms.values())

        evidence = {
            "window_events": count,
            "window_declines": bin_state.declines_in_window,
            "window_decline_ratio": decline_ratio,
            "baseline_decline_ratio": baseline_decline,
            "velocity_z": velocity_bin,
            "baseline_window_events": (
                bin_state.ewma_count.value if cfg.use_ewma else self._global_count.mean
            ),
            "window_distinct_cards": distinct_cards,
            "cards_per_event": cards_per_event,
            "window_distinct_merchants": distinct_merchants,
            "window_amount_mean_paise": amount_mean,
            "baseline_amount_mean_paise": baseline_amount,
            "window_saturated": bin_state.saturated,
            "terms": terms,
        }
        return score, evidence

    # --- the streaming surface --------------------------------------------

    def update(self, event: Event) -> Flag | None:
        score, evidence = self._advance(event)
        if score <= self.config.threshold:
            return None
        terms = evidence.pop("terms")
        return Flag(
            ts=event.ts,
            txn_id=event.txn_id,
            merchant_id=event.merchant_id,
            bin=event.bin,
            score=score,
            threshold=self.config.threshold,
            contributions=tuple(sorted(terms.items(), key=lambda kv: -abs(kv[1]))),
            **evidence,
        )

    def _advance(self, event: Event) -> tuple[float, dict]:
        cfg = self.config
        cutoff = event.ts - cfg.window_ms
        declined = event.status == STATUS_DECLINED
        amount = float(event.amount_paise)

        axis_values = {
            "bin": event.bin,
            "ip": event.ip,
            "device": event.device_id,
            "merchant": event.merchant_id,
        }
        axis_states: dict[str, EntityState] = {}
        for axis, value in axis_values.items():
            state = self._entity(axis, value)
            state.evict_before(cutoff)
            state.push(event.ts, declined, amount, event.card_ref, event.merchant_id)
            axis_states[axis] = state

        score, evidence = self._score(event, axis_states)

        # Baselines fold *after* scoring, so an event is never compared against a
        # baseline that already contains it.
        for state in axis_states.values():
            state.fold_baseline(event.ts, cfg.baseline_sample_interval_ms)
        if event.ts - getattr(self, "_last_global_ms", -(1 << 62)) >= cfg.baseline_sample_interval_ms:
            self._last_global_ms = event.ts
            self._global_count.update(float(len(axis_states["bin"].events)))

        return score, evidence

    # --- the batch surface -------------------------------------------------

    def score_batch(self, events: list[Event]) -> np.ndarray:
        """Identical arithmetic to `update`, run over a whole stream.

        Same state machine, same order. `test_streaming_matches_batch` asserts
        the two agree exactly rather than approximately.
        """
        out = np.empty(len(events), dtype=np.float64)
        for i, event in enumerate(events):
            out[i], _ = self._advance(event)
        return out
