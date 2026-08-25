"""The Rust core behind the Phase 2 `Detector` seam.

`RustDetector` is a thin adapter: it columnarises `Event` lists into NumPy
arrays (numeric columns borrowed zero-copy by the extension; identifier strings
converted, which the benchmark counts rather than excuses) and hands them to
`spandan_core.Detector`. No scoring logic lives here — a divergence between
engines must have exactly one suspect, the Rust core, not this file.

`update` is deliberately score-only. The Python `ReferenceDetector.update`
returns a rich `Flag`; the Rust surface returns the score, and the eval harness
consumes only scores. Phase 5's explanation layer keeps using the reference's
flags, so nothing downstream loses evidence by running ENGINE=rust.
"""

from __future__ import annotations

import numpy as np

import spandan_core

from ..gen.schema import STATUS_DECLINED, Event
from .interface import DetectorConfig


def _config_kwargs(config: DetectorConfig) -> dict:
    return {
        "window_ms": config.window_ms,
        "ring_capacity": config.ring_capacity,
        "baseline_sample_interval_ms": config.baseline_sample_interval_ms,
        "baseline_min_samples": config.baseline_min_samples,
        "ewma_halflife_samples": config.ewma_halflife_samples,
        "w_velocity_bin": config.w_velocity_bin,
        "w_decline_bin": config.w_decline_bin,
        "w_amount": config.w_amount,
        "w_velocity_ip": config.w_velocity_ip,
        "w_repetition_damping": config.w_repetition_damping,
        "w_merchant_span_damping": config.w_merchant_span_damping,
        "threshold": config.threshold,
        "use_ewma": config.use_ewma,
        "use_per_ip": config.use_per_ip,
    }


def columnarise(events: list[Event]) -> dict:
    """Split an event list into the columns the extension takes.

    Exposed (not private) because the benchmark measures this step separately:
    it is part of the Rust engine's true cost on list[Event] input, and hiding
    it inside the timer of neither engine would flatter Rust.
    """
    return {
        "ts": np.fromiter((e.ts for e in events), dtype=np.int64, count=len(events)),
        "amount_paise": np.fromiter(
            (e.amount_paise for e in events), dtype=np.int64, count=len(events)
        ),
        "declined": np.fromiter(
            (e.status == STATUS_DECLINED for e in events), dtype=np.bool_, count=len(events)
        ),
        "txn_id": [e.txn_id for e in events],
        "merchant_id": [e.merchant_id for e in events],
        "bin": [e.bin for e in events],
        "card_ref": [e.card_ref for e in events],
        "ip": [e.ip for e in events],
        "device_id": [e.device_id for e in events],
    }


class RustDetector:
    """`spandan_core.Detector` behind the reference's interface."""

    def __init__(self, config: DetectorConfig | None = None) -> None:
        self.config = config or DetectorConfig()
        self._native = spandan_core.Detector(**_config_kwargs(self.config))

    def reset(self) -> None:
        self._native.reset()

    def update(self, event: Event) -> float:
        return self._native.update(
            event.ts,
            event.txn_id,
            event.merchant_id,
            event.bin,
            event.card_ref,
            event.ip,
            event.device_id,
            event.amount_paise,
            event.status,
        )

    def score_batch(self, events: list[Event]) -> np.ndarray:
        if not events:
            return np.empty(0, dtype=np.float64)
        return self._native.score_batch(**columnarise(events))

    # Introspection for the memory measurement.
    def entity_count(self) -> int:
        return self._native.entity_count()

    def buffered_events(self) -> int:
        return self._native.buffered_events()

    def saturated_entities(self) -> int:
        return self._native.saturated_entities()


ENGINES = ("python", "rust")


def make_detector(engine: str, config: DetectorConfig | None = None):
    """The one place engine names resolve to implementations."""
    if engine == "python":
        from .reference import ReferenceDetector

        return ReferenceDetector(config)
    if engine == "rust":
        return RustDetector(config)
    raise ValueError(f"unknown engine {engine!r}; expected one of {ENGINES}")
