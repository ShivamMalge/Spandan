"""The four labeled scenarios, as statistical signatures.

Per `agents.md` §7 these are labeled test fixtures for a detector. Each is
described by its signature only — event rate, how concentrated the entity axes
are, the amount band, and the decline ratio — because that is exactly what a
detector keys on and it is all the evaluation needs. Nothing here is a procedure.

The four populations:

- **burst** (label 1) — one BIN, one IP, one device, many distinct cards, a tight
  low-amount band, a high decline ratio, minutes of duration. The easy case, and
  the one a naive per-IP velocity rule catches.
- **rotating** (label 1) — the same concentration on the BIN and card axes, but
  the IP and device axes are spread across dozens of values, so no single address
  carries unusual volume. Included because a per-IP rule's recall on this is the
  interesting number.
- **slow_low** (label 1) — the same entity concentration stretched across hours at
  a rate that sits underneath a fixed per-window count. Expected to be the hardest
  case; it is here so the difficulty gets measured instead of asserted.
- **flash_sale** (label 0) — a genuine volume surge from genuine customers. Many
  distinct cards, IPs and devices, ordinary amounts, and a mildly elevated decline
  ratio consistent with gateway strain. Its customers are a *mixture*: mostly
  known customers drawn with the same popularity weights as benign traffic, plus a
  minority of genuinely new ones. It looks like an attack on every volume-shaped
  feature and is labeled clean. The false-positive cost model rests on this one.
"""

from __future__ import annotations

import math

import numpy as np

from . import entities
from .baseline import EntityPools, _draw_amounts
from .config import MS_PER_DAY, MS_PER_MINUTE, STREAM_START_MS, GenConfig, ScenarioEpisodeSpec
from .entities import Merchant
from .schema import (
    ATTACK_SCENARIOS,
    SCENARIO_FLASH_SALE,
    STATUS_APPROVED,
    STATUS_DECLINED,
    Event,
)

#: Scenario identifiers get their own card and device token ranges, well clear of
#: the benign pool, so a scenario card is never accidentally a benign card.
_SCENARIO_CARD_OFFSET = 50_000_000
_SCENARIO_DEVICE_OFFSET = 50_000_000


def generate_episode(
    spec: ScenarioEpisodeSpec,
    index: int,
    cfg: GenConfig,
    pools: EntityPools,
    merchants: list[Merchant],
    rng: np.random.Generator,
) -> list[Event]:
    merchant = merchants[spec.merchant_index % len(merchants)]

    start_ms = STREAM_START_MS + int(spec.day * MS_PER_DAY)
    span_ms = int(spec.duration_minutes * MS_PER_MINUTE)
    offsets = np.sort(rng.integers(0, max(span_ms, 1), size=spec.event_count))
    timestamps = start_ms + offsets

    if spec.scenario_id == SCENARIO_FLASH_SALE:
        cards, ips, devices, bins_ = _flash_sale_entities(spec, index, cfg, pools, rng)
        amounts = _flash_sale_amounts(spec, cfg, merchant, rng)
    else:
        cards, ips, devices, bins_ = _attack_entities(spec, index, pools, rng)
        amounts = rng.integers(
            spec.amount_min_paise, spec.amount_max_paise + 1, size=spec.event_count
        )

    declined = rng.random(spec.event_count) < spec.decline_ratio
    label = 1 if spec.scenario_id in ATTACK_SCENARIOS else 0

    card_pick = rng.integers(0, len(cards), size=spec.event_count)
    ip_pick = rng.integers(0, len(ips), size=spec.event_count)
    device_pick = rng.integers(0, len(devices), size=spec.event_count)
    bin_pick = rng.integers(0, len(bins_), size=spec.event_count)

    return [
        Event(
            ts=int(timestamps[i]),
            txn_id="",
            merchant_id=merchant.merchant_id,
            bin=bins_[int(bin_pick[i])],
            card_ref=cards[int(card_pick[i])],
            ip=ips[int(ip_pick[i])],
            device_id=devices[int(device_pick[i])],
            amount_paise=int(amounts[i]),
            status=STATUS_DECLINED if declined[i] else STATUS_APPROVED,
            label=label,
            scenario_id=spec.scenario_id,
        )
        for i in range(spec.event_count)
    ]


def _attack_entities(
    spec: ScenarioEpisodeSpec,
    index: int,
    pools: EntityPools,
    rng: np.random.Generator,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Fresh cards, fresh addresses, and one BIN that also exists in benign traffic.

    The BIN is drawn from the benign pool rather than invented. That is both more
    realistic — an issuer whose cards are being tested still has ordinary
    customers — and strictly harder for the detector, because the BIN has a
    legitimate baseline to be measured against instead of appearing from nowhere.
    """
    card_base = _SCENARIO_CARD_OFFSET + index * 100_000
    device_base = _SCENARIO_DEVICE_OFFSET + index * 100_000

    cards = entities.make_card_refs(spec.distinct_cards, offset=card_base)
    devices = entities.make_device_ids(spec.distinct_devices, offset=device_base)
    ips = entities.make_ips(rng, spec.distinct_ips)
    bins_ = [pools.bins[int(rng.integers(0, len(pools.bins)))]]
    return cards, ips, devices, bins_


#: Fresh identifiers for the new-customer share of a flash sale, kept clear of
#: both the benign pool and the attack episodes' ranges.
_FLASH_NEW_CARD_OFFSET = 70_000_000
_FLASH_NEW_DEVICE_OFFSET = 70_000_000


def _flash_sale_entities(
    spec: ScenarioEpisodeSpec,
    index: int,
    cfg: GenConfig,
    pools: EntityPools,
    rng: np.random.Generator,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """A mixture of known customers and genuinely new ones.

    The known share is drawn with the *same popularity weights* as benign traffic,
    not uniformly over the pool. That distinction matters: a uniform draw over a
    Zipf-shaped pool mostly selects cards that never actually transacted, so the
    sale would consist largely of identifiers unseen in the stream and would be
    separable by novelty alone — which would quietly destroy the only
    false-positive test in the project.

    The remaining `flash_sale_new_entity_fraction` are first-time customers with
    unseen cards, addresses and devices, because real sales bring those too and a
    detector must not treat "unseen card" as sufficient evidence.
    """
    new_share = cfg.flash_sale_new_entity_fraction

    def mixture(pool: list[str], weights, count: int, fresh: list[str]) -> list[str]:
        new_count = min(len(fresh), int(round(count * new_share)))
        known_count = max(count - new_count, 0)
        known_idx = rng.choice(len(pool), size=min(known_count, len(pool)), replace=False, p=weights)
        return [pool[int(i)] for i in known_idx] + fresh[:new_count]

    card_base = _FLASH_NEW_CARD_OFFSET + index * 100_000
    device_base = _FLASH_NEW_DEVICE_OFFSET + index * 100_000
    fresh_cards = entities.make_card_refs(spec.distinct_cards, offset=card_base)
    fresh_devices = entities.make_device_ids(spec.distinct_devices, offset=device_base)
    fresh_ips = entities.make_ips(rng, spec.distinct_ips)

    cards = mixture(pools.cards, pools.card_weights, spec.distinct_cards, fresh_cards)
    ips = mixture(pools.ips, pools.ip_weights, spec.distinct_ips, fresh_ips)
    devices = mixture(pools.devices, pools.device_weights, spec.distinct_devices, fresh_devices)

    # Many issuers, weighted like ordinary traffic: a sale is not BIN-concentrated.
    bin_count = min(len(pools.bins), max(24, spec.distinct_cards // 30))
    bin_idx = rng.choice(len(pools.bins), size=bin_count, replace=False, p=pools.bin_weights)
    bins_ = [pools.bins[int(i)] for i in bin_idx]
    return cards, ips, devices, bins_


def _flash_sale_amounts(
    spec: ScenarioEpisodeSpec,
    cfg: GenConfig,
    merchant: Merchant,
    rng: np.random.Generator,
) -> np.ndarray:
    """Ordinary basket sizes, discounted into the episode's band.

    Drawn from the merchant's own lognormal rather than uniformly, so a sale
    looks like that merchant's traffic at a lower price point — not like a
    distribution nobody has ever seen.
    """
    raw = _draw_amounts(rng, merchant, cfg, spec.event_count)
    scaled = raw * (spec.amount_min_paise / max(merchant.amount_median_paise, 1)) * 3.0
    return np.clip(scaled, spec.amount_min_paise, spec.amount_max_paise).astype(np.int64)


def generate_all_episodes(
    cfg: GenConfig,
    pools: EntityPools,
    merchants: list[Merchant],
    seeds: list[np.random.SeedSequence],
) -> list[Event]:
    """One independent RNG stream per episode.

    Independent so that adding, removing or retuning one episode does not shift
    the draws of every other episode — otherwise every scenario edit silently
    rewrites the whole dataset and no before/after comparison means anything.
    """
    if len(seeds) != len(cfg.episodes):
        raise ValueError("one seed sequence per episode is required")

    events: list[Event] = []
    for index, (spec, seed) in enumerate(zip(cfg.episodes, seeds)):
        events.extend(
            generate_episode(spec, index, cfg, pools, merchants, np.random.default_rng(seed))
        )
    return events


def episode_windows(cfg: GenConfig) -> list[tuple[str, int, int]]:
    """(scenario_id, start_ms, end_ms) per episode, for the manifest."""
    out = []
    for spec in cfg.episodes:
        start = STREAM_START_MS + int(spec.day * MS_PER_DAY)
        end = start + int(math.ceil(spec.duration_minutes * MS_PER_MINUTE))
        out.append((spec.scenario_id, start, end))
    return out
