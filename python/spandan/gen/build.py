"""Assemble the stream, split it temporally, write it, and record what was done.

`python -m spandan.gen.build` is what `make data` runs.

Two properties this module is responsible for and that the tests check:

1. **Byte-identical regeneration.** Same seed, same bytes. gzip stamps an mtime
   into its header by default, which would defeat that on its own, so the writer
   pins `mtime=0`.
2. **A strictly temporal split.** Train is everything before the boundary, test is
   everything at or after it, and no event straddles it. Not a shuffle, not a
   stratified sample, not a k-fold — `agents.md` §6.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from .baseline import EntityPools, build_merchants, generate_benign
from .config import DEFAULT_CONFIG, GenConfig
from .scenarios import episode_windows, generate_all_episodes
from .schema import (
    ALL_COLUMNS,
    ATTACK_SCENARIOS,
    SCENARIO_BENIGN,
    SCENARIO_FLASH_SALE,
    SCENARIOS,
    STATUS_DECLINED,
    Event,
    to_record,
)

TRAIN_FILENAME = "train.jsonl.gz"
TEST_FILENAME = "test.jsonl.gz"
MANIFEST_FILENAME = "manifest.json"


def generate_events(cfg: GenConfig) -> list[Event]:
    """The full stream, time-ordered, with transaction ids assigned."""
    root = np.random.SeedSequence(cfg.seed)
    pool_seed, merchant_seed, benign_seed, episode_parent = root.spawn(4)
    episode_seeds = episode_parent.spawn(len(cfg.episodes))

    pools = EntityPools(cfg, np.random.default_rng(pool_seed))
    merchants = build_merchants(cfg, np.random.default_rng(merchant_seed))

    events = generate_benign(cfg, pools, merchants, np.random.default_rng(benign_seed))
    events.extend(generate_all_episodes(cfg, pools, merchants, list(episode_seeds)))

    # A total order, not just a sort by time: ties must break the same way on
    # every run or the output is not byte-identical.
    events.sort(key=lambda e: (e.ts, e.scenario_id, e.merchant_id, e.card_ref, e.ip))
    return [replace(e, txn_id=f"txn_{i:09d}") for i, e in enumerate(events)]


def split_temporally(events: list[Event], boundary_ms: int) -> tuple[list[Event], list[Event]]:
    train = [e for e in events if e.ts < boundary_ms]
    test = [e for e in events if e.ts >= boundary_ms]
    return train, test


def write_stream(path: Path, events: list[Event]) -> str:
    """Write gzipped JSONL and return the sha256 of the file.

    `mtime=0` because gzip otherwise writes the current time into the header and
    two identical runs would produce different bytes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as gz:
            for event in events:
                line = json.dumps(to_record(event), separators=(",", ":"))
                gz.write(line.encode("utf-8"))
                gz.write(b"\n")
    return sha256_of(path)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _split_summary(events: list[Event]) -> dict:
    scenario_counts = Counter(e.scenario_id for e in events)
    benign = [e for e in events if e.scenario_id == SCENARIO_BENIGN]
    benign_declines = sum(1 for e in benign if e.status == STATUS_DECLINED)
    positives = sum(e.label for e in events)
    return {
        "rows": len(events),
        "ts_min": min((e.ts for e in events), default=None),
        "ts_max": max((e.ts for e in events), default=None),
        "positives": positives,
        "positive_rate": round(positives / len(events), 6) if events else 0.0,
        "scenario_counts": {name: scenario_counts.get(name, 0) for name in SCENARIOS},
        "scenario_positives": {
            name: sum(1 for e in events if e.scenario_id == name and e.label == 1)
            for name in SCENARIOS
        },
        "benign_rows": len(benign),
        "benign_decline_rate": round(benign_declines / len(benign), 6) if benign else 0.0,
        "distinct_bins": len({e.bin for e in events}),
        "distinct_ips": len({e.ip for e in events}),
        "distinct_devices": len({e.device_id for e in events}),
        "distinct_cards": len({e.card_ref for e in events}),
        "distinct_merchants": len({e.merchant_id for e in events}),
    }


def _negative_case_summary(events: list[Event]) -> dict:
    """How hard the flash sale actually is, measured rather than assumed.

    The realised known-customer share is the number that matters, and it is not
    the configured one: the flash sale draws its known share with benign
    popularity weights, but a weighted draw over a large pool still selects some
    cards that never transact. Recorded per build so the negative case cannot
    silently drift into being novelty-separable — which it was once already, see
    BUILD_LOG.
    """
    benign_cards = {e.card_ref for e in events if e.scenario_id == SCENARIO_BENIGN}
    flash_cards = {e.card_ref for e in events if e.scenario_id == SCENARIO_FLASH_SALE}
    attack_cards = {e.card_ref for e in events if e.label == 1}
    if not flash_cards:
        return {}
    known = len(flash_cards & benign_cards)
    return {
        "flash_sale_distinct_cards": len(flash_cards),
        "flash_sale_known_customer_share": round(known / len(flash_cards), 6),
        "flash_sale_new_customer_share": round(1 - known / len(flash_cards), 6),
        "configured_new_entity_fraction": None,
        "attack_cards_seen_in_benign": len(attack_cards & benign_cards),
        "attack_distinct_cards": len(attack_cards),
    }


def build(cfg: GenConfig = DEFAULT_CONFIG, out_dir: Path | str = "data") -> dict:
    out = Path(out_dir)
    events = generate_events(cfg)
    train, test = split_temporally(events, cfg.train_end_ms)
    negative_case = _negative_case_summary(events)
    if negative_case:
        negative_case["configured_new_entity_fraction"] = cfg.flash_sale_new_entity_fraction

    train_sha = write_stream(out / TRAIN_FILENAME, train)
    test_sha = write_stream(out / TEST_FILENAME, test)

    manifest = {
        "seed": cfg.seed,
        "config_hash": cfg.config_hash(),
        "config": asdict(cfg),
        "split": {
            "kind": "temporal",
            "boundary_ms": cfg.train_end_ms,
            "train_max_ts": max((e.ts for e in train), default=None),
            "test_min_ts": min((e.ts for e in test), default=None),
            "train_strictly_precedes_test": (
                bool(train) and bool(test) and max(e.ts for e in train) < min(e.ts for e in test)
            ),
        },
        "declared_benign_decline_band": [
            cfg.benign_decline_rate_min,
            cfg.benign_decline_rate_max,
        ],
        "attack_scenarios": list(ATTACK_SCENARIOS),
        "negative_case": negative_case,
        "files": {
            "train": {"path": TRAIN_FILENAME, "sha256": train_sha, "rows": len(train)},
            "test": {"path": TEST_FILENAME, "sha256": test_sha, "rows": len(test)},
        },
        "train": _split_summary(train),
        "test": _split_summary(test),
        "episode_windows": [
            {"scenario_id": name, "start_ms": start, "end_ms": end}
            for name, start, end in episode_windows(cfg)
        ],
        "columns": {"all": list(ALL_COLUMNS)},
    }

    manifest_path = out / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def read_stream(path: Path | str) -> list[Event]:
    """Read a written stream back. Used by the summary and by the tests."""
    from .schema import from_record

    events = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            events.append(from_record(json.loads(line)))
    return events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the Spandan synthetic stream.")
    parser.add_argument("--out", default="data", help="output directory (default: data)")
    parser.add_argument(
        "--seed", type=int, default=None, help="override the config seed (default: config seed)"
    )
    args = parser.parse_args(argv)

    cfg = DEFAULT_CONFIG if args.seed is None else replace(DEFAULT_CONFIG, seed=args.seed)
    manifest = build(cfg, args.out)

    files = manifest["files"]
    print(f"seed          {manifest['seed']}")
    print(f"config_hash   {manifest['config_hash'][:16]}")
    print(f"train         {files['train']['rows']:>7} rows  sha256 {files['train']['sha256'][:16]}")
    print(f"test          {files['test']['rows']:>7} rows  sha256 {files['test']['sha256'][:16]}")
    print(f"written to    {Path(args.out).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
