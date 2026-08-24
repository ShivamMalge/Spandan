"""Deterministic episode scheduling.

Phase 2 found that six attack episodes in the test window could not support a
per-scenario claim: recall swung 0.16-0.54 across seeds because every
per-scenario number rested on a sample of two. The fix is more episodes, and the
constraint on that fix is that it must raise statistical power **without**
touching the detector, the threshold rule, or the difficulty of the scenarios.

So episodes are scheduled at an unchanged **rate per day** and the stream is made
longer, rather than being packed more densely into the same span. Densifying would
change the character of the traffic: more attack episodes per day means more
attack traffic folded into the per-BIN baselines those attacks are measured
against, which is a difficulty change wearing a statistics costume.

Every per-episode parameter is drawn from the same range the hand-written
episodes used, so the scenarios themselves are exactly as hard as they were.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import ScenarioEpisodeSpec

#: Episodes per scenario per day. Matches the hand-written schedule's density
#: (burst was 6 episodes over 14 days = 0.43/day; the others 0.29-0.36/day).
#: Held constant when the span changes - that is the whole point of this module.
EPISODES_PER_DAY = 0.40


@dataclass(frozen=True, slots=True)
class ScenarioTemplate:
    """The range each parameter is drawn from. Unchanged from Phase 1."""

    scenario_id: str
    duration_minutes: tuple[float, float]
    event_count: tuple[int, int]
    cards_per_event: tuple[float, float]
    """Distinct cards as a fraction of events. 1.0 = every event a different
    card (a probe run); 0.3 = roughly three attempts per card (retries)."""
    ips_per_event: tuple[float, float]
    devices_per_event: tuple[float, float]
    decline_ratio: tuple[float, float]
    amount_paise: tuple[int, int]
    merchant_span: tuple[int, int]


TEMPLATES: tuple[ScenarioTemplate, ...] = (
    # --- attacks (label 1) --------------------------------------------------
    ScenarioTemplate(
        "burst", (9.0, 15.0), (190, 300), (1.0, 1.0), (0.004, 0.006), (0.004, 0.006),
        (0.83, 0.89), (100, 6_000), (1, 1),
    ),
    ScenarioTemplate(
        "rotating", (22.0, 31.0), (205, 285), (1.0, 1.0), (0.20, 0.27), (0.19, 0.25),
        (0.81, 0.86), (100, 6_000), (1, 1),
    ),
    ScenarioTemplate(
        "slow_low", (330.0, 420.0), (48, 66), (1.0, 1.0), (0.045, 0.08), (0.045, 0.07),
        (0.69, 0.75), (100, 4_000), (1, 1),
    ),
    # --- negative controls (label 0) ---------------------------------------
    ScenarioTemplate(
        "flash_sale", (45.0, 60.0), (760, 1_020), (0.96, 0.99), (0.92, 0.94), (0.89, 0.92),
        (0.14, 0.17), (15_000, 900_000), (1, 1),
    ),
    ScenarioTemplate(
        "issuer_outage", (55.0, 78.0), (760, 1_050), (0.30, 0.34), (0.29, 0.32),
        (0.28, 0.31), (0.79, 0.86), (1_000, 500_000), (4, 5),
    ),
    # The variant Phase 2 review asked for: a single merchant, and amounts in the
    # same low band as a probe burst. It strips away the two separators the
    # detector was actually using to reject the multi-merchant outage - merchant
    # span and amount - and leaves only the retry structure. This is the outage a
    # payments panel pictures, and it is the harder control by a wide margin.
    ScenarioTemplate(
        "outage_single_merchant", (55.0, 78.0), (760, 1_050), (0.18, 0.24), (0.17, 0.22),
        (0.16, 0.21), (0.79, 0.86), (100, 6_000), (1, 1),
    ),
)


def build_schedule(
    total_days: int,
    train_days: int,
    seed: int,
    episodes_per_day: float = EPISODES_PER_DAY,
) -> tuple[ScenarioEpisodeSpec, ...]:
    """Every episode for the whole stream, deterministic in `seed`.

    Episodes are laid out independently per split, so both the training period
    and the test window carry the same number per scenario. The training period
    needs them because Phase 2 selects its threshold on a validation window
    carved from it; the test window needs them because two episodes is an
    anecdote.
    """
    rng = np.random.default_rng(np.random.SeedSequence([seed, 0x5CEDD1E]))
    test_days = total_days - train_days
    episodes: list[ScenarioEpisodeSpec] = []

    for template in TEMPLATES:
        for start_day, span_days in ((0.0, train_days), (float(train_days), test_days)):
            count = max(1, int(round(span_days * episodes_per_day)))
            episodes.extend(
                _draw_episode(template, start_day, span_days, index, count, rng)
                for index in range(count)
            )

    episodes.sort(key=lambda spec: (spec.day, spec.scenario_id))
    return tuple(episodes)


def _draw_episode(
    template: ScenarioTemplate,
    start_day: float,
    span_days: float,
    index: int,
    count: int,
    rng: np.random.Generator,
) -> ScenarioEpisodeSpec:
    """One episode, jittered inside its slot.

    Slotted rather than uniformly random so episodes cannot clump by accident and
    silently create a density spike - the exact thing this module exists to avoid.
    """
    duration = float(rng.uniform(*template.duration_minutes))
    slot = span_days / count
    # Leave room at the end of the slot so no episode runs past the split boundary.
    latest = max(slot - duration / 1440.0 - 0.01, 0.0)
    day = start_day + index * slot + float(rng.uniform(0.0, latest))

    events = int(rng.integers(template.event_count[0], template.event_count[1] + 1))
    lo, hi = template.amount_paise

    return ScenarioEpisodeSpec(
        scenario_id=template.scenario_id,
        day=round(day, 6),
        duration_minutes=round(duration, 3),
        event_count=events,
        distinct_cards=max(1, int(round(events * rng.uniform(*template.cards_per_event)))),
        distinct_ips=max(1, int(round(events * rng.uniform(*template.ips_per_event)))),
        distinct_devices=max(1, int(round(events * rng.uniform(*template.devices_per_event)))),
        decline_ratio=round(float(rng.uniform(*template.decline_ratio)), 4),
        amount_min_paise=lo,
        amount_max_paise=hi,
        merchant_index=int(rng.integers(0, 10)),
        merchant_span=int(rng.integers(template.merchant_span[0], template.merchant_span[1] + 1)),
    )


def describe_schedule(episodes: tuple[ScenarioEpisodeSpec, ...], train_days: int) -> dict:
    """Episodes per scenario per split, for the manifest and the summary."""
    out: dict[str, dict[str, int]] = {}
    for spec in episodes:
        split = "train" if spec.day < train_days else "test"
        out.setdefault(spec.scenario_id, {"train": 0, "test": 0})[split] += 1
    return out
