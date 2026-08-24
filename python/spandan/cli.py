"""`spandan replay` — the demo surface.

Streams the test set through the detector one event at a time, exactly as a live
integration would, and prints flags with running rupee exposure. This is the
streaming path, not a batch scoring pass dressed up: it calls `Detector.update`
per event, so what the demo shows is what the streaming API does.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .detect import DetectorConfig, ReferenceDetector
from .eval.costs import CostModel
from .gen.build import TEST_FILENAME, TRAIN_FILENAME, read_stream
from .gen.schema import STATUS_APPROVED, ATTACK_SCENARIOS

BAR = "=" * 78


def _utf8() -> str:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            return "Rs "
    return "₹"


def replay(argv: list[str] | None = None) -> int:
    R = _utf8()
    parser = argparse.ArgumentParser(prog="spandan replay")
    parser.add_argument("stream", nargs="?", default=None, help="path to a .jsonl.gz stream")
    parser.add_argument("--data", default="data")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--engine", choices=("python", "rust"), default="python")
    parser.add_argument("--quiet", action="store_true", help="only the summary")
    parser.add_argument(
        "--cold-start",
        action="store_true",
        help="skip baseline warmup, to demonstrate cold-start false positives",
    )
    args = parser.parse_args(argv)

    if args.engine == "rust":
        print("the rust engine arrives in phase 4; running python", file=sys.stderr)

    data_dir = Path(args.data)
    stream_path = Path(args.stream) if args.stream else data_dir / TEST_FILENAME

    threshold = args.threshold
    if threshold is None:
        metrics_path = data_dir / "metrics.json"
        if metrics_path.exists():
            threshold = json.loads(metrics_path.read_text(encoding="utf-8"))["threshold"]
        else:
            threshold = DetectorConfig().threshold
            print(
                f"no {metrics_path} - using the placeholder threshold {threshold}. "
                "Run `make eval` to select one on the validation window.",
                file=sys.stderr,
            )

    events = read_stream(stream_path)
    if args.limit:
        events = events[: args.limit]

    model = CostModel.load()
    detector = ReferenceDetector(DetectorConfig(threshold=threshold))

    print(BAR)
    print(f"SPANDAN replay  {stream_path.name}  {len(events):,} events  threshold {threshold:.2f}")
    print(BAR)

    # Warm the per-entity baselines on the training window before replaying,
    # exactly as `make eval` does. Without this the detector starts with empty
    # baselines and its first minutes are cold-start artifacts rather than
    # detections - which is a real property worth showing on purpose
    # (`--cold-start`) and misleading to show by accident.
    if not args.cold_start:
        warmup_path = data_dir / TRAIN_FILENAME
        if warmup_path.exists():
            warmup = read_stream(warmup_path)
            detector.score_batch(warmup)
            print(f"baselines warmed on {len(warmup):,} earlier events from {warmup_path.name}")
            print(BAR)
        else:
            print(
                f"no {warmup_path} to warm on - running cold, expect early false positives",
                file=sys.stderr,
            )
    else:
        print("COLD START: no warmup. Early flags here are the cost of an empty baseline,")
        print("not detections. This is the failure mode, shown deliberately.")
        print(BAR)

    exposure_paise = 0.0
    flags = 0
    true_flags = 0
    last_alert_key = None
    per_scenario: dict[str, int] = {}

    for event in events:
        flag = detector.update(event)
        if flag is None:
            continue
        flags += 1
        per_scenario[event.scenario_id] = per_scenario.get(event.scenario_id, 0) + 1
        if event.label == 1:
            true_flags += 1
            if event.status == STATUS_APPROVED:
                exposure_paise += (
                    model.chargeback_fee_paise
                    + model.chargeback_loss_fraction * event.amount_paise
                ) * model.chargeback_rate_on_approved_fraud
            exposure_paise += model.auth_fee_paise

        key = (flag.merchant_id, flag.bin)
        if args.quiet or key == last_alert_key:
            continue
        last_alert_key = key

        top = flag.contributions[0] if flag.contributions else ("-", 0.0)
        print()
        print(
            f"[FLAG] {flag.merchant_id}  BIN {flag.bin}  score {flag.score:.1f} "
            f"(> {flag.threshold:.1f})"
        )
        print(
            f"       window {flag.window_events} events, "
            f"{flag.window_decline_ratio:.0%} declined "
            f"(baseline {flag.baseline_decline_ratio:.0%}), "
            f"{flag.window_distinct_cards} distinct cards"
        )
        print(
            f"       velocity {flag.velocity_z:.1f}sd above baseline of "
            f"{flag.baseline_window_events:.1f} events/window; "
            f"mean amount {R}{flag.window_amount_mean_paise/100:,.0f} "
            f"vs baseline {R}{flag.baseline_amount_mean_paise/100:,.0f}"
        )
        print(f"       strongest evidence: {top[0]} ({top[1]:+.1f} of {flag.score:.1f})")
        print(f"       running exposure prevented: {R}{exposure_paise/100:,.0f}")

    print()
    print(BAR)
    print("REPLAY SUMMARY")
    print(BAR)
    print(f"{'events replayed':<34}{len(events):>16,}")
    print(f"{'flagged':<34}{flags:>16,}")
    print(f"{'of which truly card testing':<34}{true_flags:>16,}")
    print(f"{'exposure prevented':<34}{R + format(exposure_paise/100, ',.0f'):>16}")
    if per_scenario:
        print()
        print("flags by scenario (labels are for this readout only; the detector never saw them):")
        for name, count in sorted(per_scenario.items(), key=lambda kv: -kv[1]):
            kind = "attack" if name in ATTACK_SCENARIOS else "CLEAN - false positive"
            print(f"  {name:<16}{count:>8}   {kind}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spandan", add_help=True)
    parser.add_argument("command", choices=("replay",), help="replay the stream through the detector")
    args, rest = parser.parse_known_args(argv)
    if args.command == "replay":
        return replay(rest)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
