"""`python -m spandan.gen.summary` — what was generated, in a form worth reading.

Prints the split shape, the class balance, the entity cardinalities, the observed
benign decline rate against its declared band, and the temporal-split assertion.
The last line is the one that matters: if `max(train.ts) < min(test.ts)` is not
True, nothing downstream is worth measuring.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from .build import MANIFEST_FILENAME
from .config import IST_OFFSET_MS
from .schema import ATTACK_SCENARIOS, SCENARIOS


def _ist(ms: int | None) -> str:
    if ms is None:
        return "-"
    moment = dt.datetime.fromtimestamp((ms + IST_OFFSET_MS) / 1000.0, tz=dt.timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M:%S IST")


def _rule(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def render(manifest: dict) -> None:
    print(f"seed          {manifest['seed']}")
    print(f"config_hash   {manifest['config_hash']}")
    print(f"split kind    {manifest['split']['kind']}")

    _rule("split shape")
    header = f"{'':<10}{'rows':>9}{'positives':>11}{'pos rate':>10}  {'first event':<26}{'last event':<26}"
    print(header)
    for name in ("train", "test"):
        s = manifest[name]
        print(
            f"{name:<10}{s['rows']:>9}{s['positives']:>11}{s['positive_rate']:>10.4f}  "
            f"{_ist(s['ts_min']):<26}{_ist(s['ts_max']):<26}"
        )

    _rule("rows per scenario (positives in brackets)")
    print(f"{'scenario':<14}{'train':>18}{'test':>18}")
    for name in SCENARIOS:
        marker = "*" if name in ATTACK_SCENARIOS else " "
        train_cell = f"{manifest['train']['scenario_counts'][name]} [{manifest['train']['scenario_positives'][name]}]"
        test_cell = f"{manifest['test']['scenario_counts'][name]} [{manifest['test']['scenario_positives'][name]}]"
        print(f"{marker} {name:<12}{train_cell:>18}{test_cell:>18}")
    print("  * = labeled 1. flash_sale is a labeled-clean volume surge: the false-positive test.")

    _rule("entity cardinalities")
    print(f"{'':<10}{'bins':>9}{'cards':>9}{'ips':>9}{'devices':>9}{'merchants':>11}")
    for name in ("train", "test"):
        s = manifest[name]
        print(
            f"{name:<10}{s['distinct_bins']:>9}{s['distinct_cards']:>9}{s['distinct_ips']:>9}"
            f"{s['distinct_devices']:>9}{s['distinct_merchants']:>11}"
        )

    _rule("benign decline rate vs declared band")
    low, high = manifest["declared_benign_decline_band"]
    print(f"declared band           [{low:.4f}, {high:.4f}]")
    for name in ("train", "test"):
        observed = manifest[name]["benign_decline_rate"]
        verdict = "in band" if low <= observed <= high else "OUT OF BAND"
        print(f"observed ({name:<5})        {observed:.4f}   {verdict}")

    negative = manifest.get("negative_case") or {}
    if negative:
        _rule("negative case: how hard is the flash sale")
        print(f"flash-sale distinct cards        {negative['flash_sale_distinct_cards']}")
        print(f"  known benign customers         {negative['flash_sale_known_customer_share']:.1%}")
        print(f"  first-time customers           {negative['flash_sale_new_customer_share']:.1%}")
        print(f"  (configured fresh-draw share   {negative['configured_new_entity_fraction']:.0%})")
        print(f"attack cards seen in benign      {negative['attack_cards_seen_in_benign']} of {negative['attack_distinct_cards']}")
        print("  A sale made only of unseen cards would be separable by novelty alone,")
        print("  which would make the false-positive test worthless. Measured, not assumed.")

    _rule("temporal split")
    split = manifest["split"]
    print(f"train_max_ts  {split['train_max_ts']}  {_ist(split['train_max_ts'])}")
    print(f"test_min_ts   {split['test_min_ts']}  {_ist(split['test_min_ts'])}")
    print(f"max(train.ts) < min(test.ts): {split['train_strictly_precedes_test']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarise a generated Spandan stream.")
    parser.add_argument("--data", default="data", help="directory holding manifest.json")
    args = parser.parse_args(argv)

    manifest_path = Path(args.data) / MANIFEST_FILENAME
    if not manifest_path.exists():
        print(f"no manifest at {manifest_path} - run `make data` first")
        return 2
    render(json.loads(manifest_path.read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
