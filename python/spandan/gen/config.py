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


@dataclass(frozen=True, slots=True)
class GenConfig:
    seed: int = 20_260_824

    # --- span and split ---------------------------------------------------
    total_days: int = 14
    train_days: int = 10
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
# Placed in both the train and the test window on purpose. Phase 2 selects its
# threshold on a validation window carved out of the training period, which is
# only possible if the training period contains positives.
#
# Signatures only. Rate, entity concentration, amount band, decline ratio.

_EPISODES: tuple[ScenarioEpisodeSpec, ...] = (
    # -- concentrated burst: one BIN, one IP, one device, low amounts, high
    #    decline ratio, compressed into minutes.
    ScenarioEpisodeSpec("burst", 1.30, 12.0, 240, 240, 1, 1, 0.86, 100, 5_000, 2),
    ScenarioEpisodeSpec("burst", 4.72, 9.0, 190, 190, 1, 1, 0.89, 100, 4_000, 5),
    ScenarioEpisodeSpec("burst", 7.15, 15.0, 300, 300, 1, 1, 0.83, 200, 6_000, 0),
    ScenarioEpisodeSpec("burst", 9.05, 11.0, 210, 210, 1, 1, 0.87, 100, 5_000, 7),
    ScenarioEpisodeSpec("burst", 10.60, 13.0, 265, 265, 1, 1, 0.85, 100, 5_000, 3),
    ScenarioEpisodeSpec("burst", 12.85, 10.0, 205, 205, 1, 1, 0.88, 200, 4_500, 6),
    # -- rotating: the same concentration on the card and BIN axes, but the IP
    #    and device axes are spread wide enough that a per-IP velocity rule sees
    #    nothing unusual on any single address.
    ScenarioEpisodeSpec("rotating", 2.45, 26.0, 250, 250, 62, 58, 0.84, 100, 5_000, 1),
    ScenarioEpisodeSpec("rotating", 5.90, 31.0, 285, 285, 74, 70, 0.81, 100, 6_000, 4),
    ScenarioEpisodeSpec("rotating", 8.35, 22.0, 205, 205, 55, 51, 0.86, 200, 4_000, 8),
    ScenarioEpisodeSpec("rotating", 11.40, 28.0, 240, 240, 66, 63, 0.83, 100, 5_500, 2),
    ScenarioEpisodeSpec("rotating", 13.10, 24.0, 220, 220, 58, 55, 0.85, 100, 5_000, 9),
    # -- slow and low: the same entity concentration stretched over hours, at a
    #    rate that stays underneath any fixed per-window count threshold. This is
    #    the scenario expected to be hardest, and it is in the plan precisely so
    #    that difficulty is measured rather than assumed.
    ScenarioEpisodeSpec("slow_low", 3.10, 330.0, 54, 54, 3, 3, 0.72, 100, 3_000, 3),
    ScenarioEpisodeSpec("slow_low", 6.55, 400.0, 62, 62, 4, 4, 0.69, 100, 3_500, 6),
    ScenarioEpisodeSpec("slow_low", 9.60, 365.0, 48, 48, 2, 2, 0.75, 200, 3_000, 1),
    ScenarioEpisodeSpec("slow_low", 11.85, 420.0, 66, 66, 5, 4, 0.70, 100, 4_000, 8),
    ScenarioEpisodeSpec("slow_low", 13.55, 380.0, 52, 52, 3, 3, 0.73, 100, 3_200, 5),
    # -- benign flash sale: labeled CLEAN. A real volume surge from real
    #    customers — many distinct cards, many distinct IPs and devices, ordinary
    #    amounts, and a mildly elevated decline ratio from gateway strain. It is
    #    shaped like an attack on every volume feature and must not be flagged.
    ScenarioEpisodeSpec("flash_sale", 2.80, 55.0, 900, 880, 840, 810, 0.16, 20_000, 900_000, 4),
    ScenarioEpisodeSpec("flash_sale", 8.70, 45.0, 760, 745, 705, 690, 0.15, 25_000, 750_000, 7),
    ScenarioEpisodeSpec("flash_sale", 11.20, 60.0, 1_020, 995, 950, 920, 0.17, 15_000, 850_000, 0),
    ScenarioEpisodeSpec("flash_sale", 12.40, 50.0, 820, 800, 765, 740, 0.14, 20_000, 800_000, 9),
)

DEFAULT_CONFIG = GenConfig(episodes=_EPISODES)
