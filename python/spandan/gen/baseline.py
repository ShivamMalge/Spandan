"""The benign baseline: ordinary traffic, with nothing wrong with it.

This is the half of the generator that decides whether the metrics mean anything.
A baseline that is too clean makes any detector look brilliant; one with no
structure makes every spike detector look broken. The shape choices here — and
the ways they differ from real traffic — are documented in `ASSUMPTIONS.md`.
"""

from __future__ import annotations

import math

import numpy as np

from . import entities
from .config import (
    IST_OFFSET_MS,
    MS_PER_DAY,
    MS_PER_HOUR,
    STREAM_START_MS,
    GenConfig,
)
from .entities import Merchant
from .schema import (
    SCENARIO_BENIGN,
    STATUS_APPROVED,
    STATUS_DECLINED,
    Event,
)


class EntityPools:
    """The benign population's identifiers.

    Scenario episodes reach into this too: a flash sale is made of *real*
    customers, so it must draw from these pools rather than from fresh
    identifiers, or it would be separable by novelty alone and would stop being
    the false-positive test it exists to be.
    """

    def __init__(self, cfg: GenConfig, rng: np.random.Generator):
        self.bins = entities.make_bins(rng, cfg.benign_bin_pool_size)
        self.cards = entities.make_card_refs(cfg.benign_card_pool_size)
        self.ips = entities.make_ips(rng, cfg.benign_ip_pool_size)
        self.devices = entities.make_device_ids(cfg.benign_device_pool_size)
        self.card_weights = _zipf_weights(len(self.cards), cfg.reuse_zipf_exponent)
        self.ip_weights = _zipf_weights(len(self.ips), cfg.reuse_zipf_exponent)
        self.device_weights = _zipf_weights(len(self.devices), cfg.reuse_zipf_exponent)
        # BIN popularity is much flatter than customer popularity: a handful of
        # issuers carry most volume, but not by a Zipf tail this steep.
        self.bin_weights = _zipf_weights(len(self.bins), 0.65)


def _zipf_weights(size: int, exponent: float) -> np.ndarray:
    """Normalised 1/i**exponent weights.

    Used instead of `Generator.zipf` because that draws from an unbounded support
    and would need clipping or modulo to fit a finite pool — both of which distort
    the tail in ways that are hard to describe honestly in ASSUMPTIONS.md.
    """
    ranks = np.arange(1, size + 1, dtype=np.float64)
    weights = ranks**-exponent
    return weights / weights.sum()


def build_merchants(cfg: GenConfig, rng: np.random.Generator) -> list[Merchant]:
    ids = entities.make_merchant_ids(cfg.merchant_count)
    rates = rng.uniform(
        cfg.merchant_min_hourly_rate, cfg.merchant_max_hourly_rate, size=cfg.merchant_count
    )
    declines = rng.uniform(
        cfg.benign_decline_rate_min, cfg.benign_decline_rate_max, size=cfg.merchant_count
    )
    medians = rng.integers(
        cfg.amount_median_min_paise, cfg.amount_median_max_paise, size=cfg.merchant_count
    )
    sigmas = rng.uniform(
        cfg.amount_log_sigma_min, cfg.amount_log_sigma_max, size=cfg.merchant_count
    )
    return [
        Merchant(
            merchant_id=ids[i],
            base_hourly_rate=float(rates[i]),
            decline_rate=float(declines[i]),
            amount_median_paise=int(medians[i]),
            amount_log_sigma=float(sigmas[i]),
        )
        for i in range(cfg.merchant_count)
    ]


def diurnal_multiplier(cfg: GenConfig, hour_index: int) -> float:
    """Volume multiplier for an absolute hour of the stream.

    A single raised cosine peaking at `diurnal_peak_hour_ist`, plus a flat weekend
    lift. Real traffic has a second, smaller lunchtime peak and merchant-specific
    shapes; this has one peak shared by every merchant. That simplification is
    recorded in ASSUMPTIONS.md.
    """
    absolute_ms = STREAM_START_MS + hour_index * MS_PER_HOUR
    ist_ms = absolute_ms + IST_OFFSET_MS
    hour_of_day = (ist_ms % MS_PER_DAY) / MS_PER_HOUR

    phase = 2.0 * math.pi * (hour_of_day - cfg.diurnal_peak_hour_ist) / 24.0
    shape = 0.5 * (1.0 + math.cos(phase))
    multiplier = cfg.diurnal_trough + (cfg.diurnal_peak - cfg.diurnal_trough) * shape

    # 2026-06-01 (stream start) is a Monday, so day_index % 7 in {5, 6} is the
    # weekend. Asserted in tests rather than trusted.
    day_index = int(ist_ms // MS_PER_DAY) - int(
        (STREAM_START_MS + IST_OFFSET_MS) // MS_PER_DAY
    )
    if day_index % 7 in (5, 6):
        multiplier *= cfg.weekend_multiplier
    return multiplier


def _draw_amounts(
    rng: np.random.Generator, merchant: Merchant, cfg: GenConfig, size: int
) -> np.ndarray:
    values = rng.lognormal(
        mean=math.log(merchant.amount_median_paise),
        sigma=merchant.amount_log_sigma,
        size=size,
    )
    return np.clip(values, cfg.amount_floor_paise, cfg.amount_ceiling_paise).astype(np.int64)


def generate_benign(
    cfg: GenConfig,
    pools: EntityPools,
    merchants: list[Merchant],
    rng: np.random.Generator,
) -> list[Event]:
    """Ordinary traffic for every merchant across the whole stream span.

    Arrivals are Poisson per merchant-hour with the diurnal multiplier applied to
    the rate, then placed uniformly inside their hour. Events carry an empty
    `txn_id`; ids are assigned in `build.py` after the global sort, so that
    transaction ids run in time order.
    """
    total_hours = cfg.total_days * 24
    multipliers = np.array(
        [diurnal_multiplier(cfg, h) for h in range(total_hours)], dtype=np.float64
    )

    events: list[Event] = []
    for merchant in merchants:
        rates = merchant.base_hourly_rate * multipliers
        counts = rng.poisson(rates)
        total = int(counts.sum())
        if total == 0:
            continue

        hour_of = np.repeat(np.arange(total_hours), counts)
        within = rng.integers(0, MS_PER_HOUR, size=total)
        timestamps = STREAM_START_MS + hour_of * MS_PER_HOUR + within

        card_idx = rng.choice(len(pools.cards), size=total, p=pools.card_weights)
        ip_idx = rng.choice(len(pools.ips), size=total, p=pools.ip_weights)
        device_idx = rng.choice(len(pools.devices), size=total, p=pools.device_weights)
        bin_idx = rng.choice(len(pools.bins), size=total, p=pools.bin_weights)

        amounts = _draw_amounts(rng, merchant, cfg, total)
        declined = rng.random(total) < merchant.decline_rate

        for i in range(total):
            events.append(
                Event(
                    ts=int(timestamps[i]),
                    txn_id="",
                    merchant_id=merchant.merchant_id,
                    bin=pools.bins[int(bin_idx[i])],
                    card_ref=pools.cards[int(card_idx[i])],
                    ip=pools.ips[int(ip_idx[i])],
                    device_id=pools.devices[int(device_idx[i])],
                    amount_paise=int(amounts[i]),
                    status=STATUS_DECLINED if declined[i] else STATUS_APPROVED,
                    label=0,
                    scenario_id=SCENARIO_BENIGN,
                )
            )
    return events
