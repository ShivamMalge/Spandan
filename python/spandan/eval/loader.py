"""Loading, and the refusal to load anything that is not a temporal split.

`agents.md` §6 forbids random splits, k-fold over shuffled data, and stratified
shuffles. The cheapest way to keep that promise is to make the loader itself
unable to hand back a split that violates it, so the rule fails loudly at the door
instead of quietly two phases later.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..gen.build import TEST_FILENAME, TRAIN_FILENAME, read_stream
from ..gen.schema import Event


class NonTemporalSplitError(RuntimeError):
    """Raised when the data on disk is not strictly time-ordered and disjoint."""


@dataclass(frozen=True, slots=True)
class Split:
    """Train, a validation window carved out of train, and test.

    `validation` is a *suffix of the training period*, never a sample of it. Every
    threshold, weight and window size in this project is selected on `validation`
    and nothing is selected on `test`.

    `train_warmup` is the part of training before the validation window. The
    detector is run over it so that per-entity baselines are warm before any
    number is read off — otherwise threshold selection would be measuring
    cold-start behaviour.
    """

    train: list[Event]
    train_warmup: list[Event]
    validation: list[Event]
    test: list[Event]
    validation_start_ms: int
    boundary_ms: int

    def describe(self) -> str:
        return (
            f"train {len(self.train):>7} events  "
            f"[warmup {len(self.train_warmup)} | validation {len(self.validation)}]\n"
            f"test  {len(self.test):>7} events"
        )


def load_split(data_dir: Path | str = "data", validation_fraction: float = 0.25) -> Split:
    data = Path(data_dir)
    train = read_stream(data / TRAIN_FILENAME)
    test = read_stream(data / TEST_FILENAME)
    return build_split(train, test, validation_fraction)


def build_split(
    train: list[Event], test: list[Event], validation_fraction: float = 0.25
) -> Split:
    if not train or not test:
        raise NonTemporalSplitError("both splits must be non-empty")

    train_max = max(e.ts for e in train)
    test_min = min(e.ts for e in test)
    if train_max >= test_min:
        raise NonTemporalSplitError(
            f"train is not strictly earlier than test: "
            f"max(train.ts)={train_max} >= min(test.ts)={test_min}. "
            "A random or shuffled split is forbidden (agents.md 6)."
        )
    for name, events in (("train", train), ("test", test)):
        stamps = [e.ts for e in events]
        if stamps != sorted(stamps):
            raise NonTemporalSplitError(f"{name} is not time-ordered")

    train_min = min(e.ts for e in train)
    span = train_max - train_min
    validation_start = train_min + int(span * (1.0 - validation_fraction))

    warmup = [e for e in train if e.ts < validation_start]
    validation = [e for e in train if e.ts >= validation_start]
    if not validation or not warmup:
        raise NonTemporalSplitError("validation window is empty; check the fraction")

    return Split(
        train=train,
        train_warmup=warmup,
        validation=validation,
        test=test,
        validation_start_ms=validation_start,
        boundary_ms=test_min,
    )
