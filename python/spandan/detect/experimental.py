"""The diagnosed fix, built as an experiment that cannot touch the reference.

FAILURE_MODES section 7 diagnoses the worst measured failure, a single-merchant
issuer outage half of whose events are flagged as card testing, to a mechanism:
retry structure is invisible at a five-minute window. An outage is the same
cards retrying over an hour; a burst is many cards once. At five minutes the
two look alike (1.13 versus 1.22 in-window attempts per card), so the
`repetition` damping term points the wrong way. The fix it names is a
long-horizon window on the BIN axis feeding that one term.

This module is that fix as a subclass. `reference.py` is not modified and the
Rust core is not touched: `LongHorizonDetector` overrides two private steps of
`ReferenceDetector`, advances a second per-BIN window before delegating, and
replaces exactly one entry of the `terms` dict the reference builds. With the
window disabled it reduces to the reference bit for bit, and inside the first
five minutes of a BIN's history, where both windows hold the same events, it
scores identically; both are tests.

Registered before any run on the test window (2026-09-03, see FAILURE_MODES
section 7 and IMPROVEMENT_PHASES Phase E): the window is 60 minutes; the term it
feeds is `repetition` and nothing else; two weights are measured, 1.2 (the hand
weight) and 6.0 (five times, the multiplier the section 9 linear model learned
for this term on the warm-up window); thresholds are chosen on validation under
the same alerts/day budget as the frozen detector; three seeds. Whether it ships
is decided by the Phase E gate, not by this module.

Memory: the long window is exact rather than ring-bounded. A BIN that sees
50,000 events in an hour holds 50,000 tuples here. That is acceptable for an
experiment that is reported and not shipped; a production version would need
the same bounded structure the reference uses, which is part of what the gate
prices in.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace

from ..gen.schema import Event
from .interface import DetectorConfig
from .reference import EntityState, ReferenceDetector

LONG_WINDOW_MS = 3_600_000
"""60 minutes. Registered; not tuned."""


@dataclass(frozen=True, slots=True)
class LongHorizonConfig:
    """The reference configuration plus the experiment's own three knobs."""

    base: DetectorConfig = DetectorConfig()
    long_window_ms: int = LONG_WINDOW_MS
    w_repetition_damping: float = 1.2
    """Weight on the long-horizon repetition term. 1.2 is the hand weight the
    reference uses on its five-minute repetition; 6.0 is the registered second
    variant."""
    enabled: bool = True
    """False reduces the detector to the reference exactly."""

    def with_(self, **changes) -> LongHorizonConfig:
        return replace(self, **changes)


class _LongWindow:
    """Events per BIN over the long horizon, with distinct-card counts kept
    incrementally the way `EntityState` keeps them."""

    __slots__ = ("events", "card_counts")

    def __init__(self) -> None:
        self.events: deque[tuple[int, str]] = deque()
        self.card_counts: dict[str, int] = {}

    def push(self, ts: int, card: str) -> None:
        self.events.append((ts, card))
        self.card_counts[card] = self.card_counts.get(card, 0) + 1

    def evict_before(self, cutoff_ms: int) -> None:
        """Window is (t - W, t], the reference's convention."""
        while self.events and self.events[0][0] <= cutoff_ms:
            _, card = self.events.popleft()
            remaining = self.card_counts[card] - 1
            if remaining:
                self.card_counts[card] = remaining
            else:
                del self.card_counts[card]

    def repetition(self) -> float:
        """The reference's arithmetic, on the long window: 1/cards_per_event - 1.
        Same expression, same operation order, so the two agree exactly when the
        windows hold the same events."""
        count = len(self.events)
        if not count:
            return 0.0
        cards_per_event = len(self.card_counts) / count
        return max(0.0, 1.0 / max(cards_per_event, 1e-9) - 1.0)


class LongHorizonDetector(ReferenceDetector):
    def __init__(self, config: LongHorizonConfig | None = None) -> None:
        self.long_config = config or LongHorizonConfig()
        super().__init__(self.long_config.base)
        self._long: dict[str, _LongWindow] = {}

    def reset(self) -> None:
        super().reset()
        self._long.clear()

    def _advance(self, event: Event) -> tuple[float, dict]:
        cfg = self.long_config
        if cfg.enabled:
            window = self._long.get(event.bin)
            if window is None:
                window = _LongWindow()
                self._long[event.bin] = window
            window.evict_before(event.ts - cfg.long_window_ms)
            window.push(event.ts, event.card_ref)
        return super()._advance(event)

    def _score(self, event: Event, axis_states: dict[str, EntityState]) -> tuple[float, dict]:
        score, evidence = super()._score(event, axis_states)
        cfg = self.long_config
        if not cfg.enabled:
            return score, evidence
        terms = evidence["terms"]
        terms["repetition"] = -cfg.w_repetition_damping * self._long[event.bin].repetition()
        evidence["long_window_events"] = len(self._long[event.bin].events)
        evidence["long_window_distinct_cards"] = len(self._long[event.bin].card_counts)
        # Same dict, same insertion order, one value replaced: the sum is the
        # reference's sum whenever the replaced value equals the original.
        return sum(terms.values()), evidence
