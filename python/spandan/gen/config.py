"""Every generator knob, in one frozen place, hashed into the manifest.

Two reasons this is a module rather than scattered literals: the manifest records
a hash of the full config so a regenerated dataset can be proved to come from the
same settings, and `gen/ASSUMPTIONS.md` has one place to point at when it explains
why each number is what it is.

Distribution shapes and their justification live in ASSUMPTIONS.md. This file is
the values only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

MS_PER_SECOND = 1_000
MS_PER_MINUTE = 60 * MS_PER_SECOND
MS_PER_HOUR = 60 * MS_PER_MINUTE
MS_PER_DAY = 24 * MS_PER_HOUR

#: Stream start: 2026-06-01T00:00:00+05:30, as epoch milliseconds UTC. A fixed
#: constant, never "now" — the dataset must be reproducible next year.
STREAM_START_MS = 1_780_252_200_000

#: The generator's clock for diurnal purposes. Indian merchants, so IST.
IST_OFFSET_MS = 5 * MS_PER_HOUR + 30 * MS_PER_MINUTE


@dataclass(frozen=True, slots=True)
class ScenarioEpisodeSpec:
    """One injected episode: when, where, and at what statistical signature.

    Described as a signature — rate, entity concentration, amount band, decline
    ratio — and nothing else. Per `agents.md` §7 these are labeled test fixtures
    for a detector, not a procedure.
    """

    scenario_id: str
    day: float
    """Offset from stream start, in days."""
    duration_minutes: float
    event_count: int
    distinct_cards: int
    distinct_ips: int
    distinct_devices: int
    decline_ratio: float
    amount_min_paise: int
    amount_max_paise: int
    merchant_index: int
    merchant_span: int = 1
    """How many merchants the episode covers, starting at `merchant_index`.

    Card testing hits one merchant at a time. An issuer outage hits every
    merchant that issuer's customers shop at simultaneously — which is one of the
    few features that separates the two, so it has to be modelled."""


@dataclass(frozen=True, slots=True)
class GenConfig:
    seed: int = 20_260_824

    # --- span and split ---------------------------------------------------
    total_days: int = 100
    train_days: int = 50
    """Train is [0, train_days); test is [train_days, total_days). Strictly
    temporal, and the boundary is a wall: no event straddles it."""

    # --- benign population ------------------------------------------------
    merchant_count: int = 10
    merchant_min_hourly_rate: float = 18.0
    merchant_max_hourly_rate: float = 72.0

    benign_decline_rate_min: float = 0.045
    benign_decline_rate_max: float = 0.115
    """Declared band for per-merchant benign decline rates. A nonzero benign
    decline rate is the whole point: a detector keyed on declines alone must have
    something to be wrong about."""

    amount_median_min_paise: int = 45_000
    amount_median_max_paise: int = 320_000
    amount_log_sigma_min: float = 0.55
    amount_log_sigma_max: float = 1.05
    amount_floor_paise: int = 1_000
    amount_ceiling_paise: int = 50_000_00

    diurnal_trough: float = 0.28
    """Multiplier at the quietest hour."""
    diurnal_peak: float = 1.95
    """Multiplier at the busiest hour."""
    diurnal_peak_hour_ist: float = 20.5

    weekend_multiplier: float = 1.18

    # --- benign entity reuse ---------------------------------------------
    benign_card_pool_size: int = 9_000
    benign_ip_pool_size: int = 6_500
    benign_device_pool_size: int = 5_200
    benign_bin_pool_size: int = 220

    reuse_zipf_exponent: float = 1.35
    """Repeat customers are common, one-time customers are the tail. A Zipf draw
    over the card pool gives natural reuse without hand-tuning a repeat rate."""

    flash_sale_new_entity_fraction: float = 0.25
    """Share of a flash sale's customers who are genuinely new — unseen card, IP
    and device.

    A sale brings both: existing customers buying earlier than they otherwise
    would, and deal-hunters arriving for the first time. Modelling it as entirely
    existing customers would make the negative case easy on the novelty axis
    (`many unseen cards` would then cleanly separate attacks from sales), and
    modelling it as entirely new customers would make it trivially separable the
    other way. The mixture is what makes the false-positive test worth running."""

    # --- injected episodes ------------------------------------------------
    episodes: tuple[ScenarioEpisodeSpec, ...] = field(default_factory=tuple)

    def config_hash(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def train_end_ms(self) -> int:
        return STREAM_START_MS + self.train_days * MS_PER_DAY

    @property
    def stream_end_ms(self) -> int:
        return STREAM_START_MS + self.total_days * MS_PER_DAY


# --- the injected episodes -------------------------------------------------
#
# Built by `schedule.build_schedule` rather than written out by hand: Phase 2
# review required 20+ episodes per scenario per split, which is 240 episodes.
#
# The span grew from 14 days to 100 (train 50 / test 50) rather than the episodes
# being packed more densely into the old span. That distinction is the whole
# point - see schedule.py. Nothing about any individual episode's signature
# changed, so the scenarios are exactly as hard as they were; there are simply
# enough of them to say something.


def default_config(seed: int = 20_260_824, total_days: int = 100, train_days: int = 50) -> GenConfig:
    from .schedule import build_schedule

    return GenConfig(
        seed=seed,
        total_days=total_days,
        train_days=train_days,
        episodes=build_schedule(total_days, train_days, seed),
    )


DEFAULT_CONFIG = default_config()
