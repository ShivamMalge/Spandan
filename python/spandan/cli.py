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

    # Both engines run behind the same seam. The rust engine's update returns a
    # bare score (evidence fields stay a reference-engine feature), so the
    # per-flag detail lines are shown only under the python engine.

    data_dir = Path(args.data)
    stream_path = Path(args.stream) if args.stream else data_dir / TEST_FILENAME

    threshold = args.threshold
    if threshold is None:
        metrics_path = data_dir / "metrics.json"
        if metrics_path.exists():
            threshold = json.loads(metrics_path.read_text(encoding="utf-8"))["threshold"]
        else:
            # Refuse rather than fall back: the config default 3.0 is a placeholder
            # that flags a fifth of all traffic, and a reader who has not run
            # `make eval` would see the detector look broken without knowing why
            # (external audit, 2026-09-03, B8).
            print(
                f"no {metrics_path}: the operating threshold is selected on the validation "
                "window by `make eval`, and this command will not replay on the config "
                "placeholder. Run `make eval` first, or pass --threshold explicitly.",
                file=sys.stderr,
            )
            return 2

    events = read_stream(stream_path)
    if args.limit:
        events = events[: args.limit]

    model = CostModel.load()
    from .detect.rust_engine import make_detector

    detector = make_detector(args.engine, DetectorConfig(threshold=threshold))

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
        if isinstance(flag, float):
            # Rust engine: a score, not a Flag. Above-threshold means flagged.
            if flag <= threshold:
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


def explain(argv: list[str] | None = None) -> int:
    """`spandan explain --flag-id <txn_id>` - one flag becomes a triage note.

    Replays the stream (reference engine: the Flag's evidence fields exist only
    there) until the named transaction is flagged, then renders the explanation
    from cassette. `--template` shows the deterministic no-LLM baseline instead,
    which is also the honest fallback a missing cassette suggests.
    """
    _utf8()
    parser = argparse.ArgumentParser(prog="spandan explain")
    parser.add_argument("--flag-id", required=True, help="txn_id of the flagged event")
    parser.add_argument("--data", default="data")
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--template", action="store_true",
                        help="render the deterministic template instead of the LLM cassette")
    parser.add_argument("--grounded-prompt", action="store_true",
                        help="use the prompt that enumerates what this pipeline does not have")
    args = parser.parse_args(argv)

    from .llm import CassetteMiss, ExplanationRejected, explain_flag, render_template

    data_dir = Path(args.data)
    threshold = args.threshold
    if threshold is None:
        metrics_path = data_dir / "metrics.json"
        threshold = (
            json.loads(metrics_path.read_text(encoding="utf-8"))["threshold"]
            if metrics_path.exists()
            else DetectorConfig().threshold
        )

    detector = ReferenceDetector(DetectorConfig(threshold=threshold))
    detector.score_batch(read_stream(data_dir / TRAIN_FILENAME))

    flag = None
    for event in read_stream(data_dir / TEST_FILENAME):
        candidate = detector.update(event)
        if candidate is not None and candidate.txn_id == args.flag_id:
            flag = candidate
            break
        if event.txn_id == args.flag_id:
            print(f"{args.flag_id} scored below the threshold {threshold:.2f}; nothing to explain")
            return 1
    if flag is None:
        print(f"{args.flag_id} not found in the test stream")
        return 1

    print(BAR)
    print(f"FLAG {flag.txn_id}  {flag.merchant_id}  BIN {flag.bin}  "
          f"score {flag.score:.2f} (> {flag.threshold:.2f})")
    print(BAR)
    if args.template:
        print(render_template(flag))
        return 0
    try:
        print(explain_flag(flag, grounded=args.grounded_prompt))
    except CassetteMiss as miss:
        print(f"[no cassette] {miss}", file=sys.stderr)
        print()
        print("deterministic template instead:")
        print()
        print(render_template(flag))
        return 3
    except ExplanationRejected as rejected:
        # The note is logged, not shown as the explanation: an analyst must not
        # receive a next action conditioned on data the system does not have.
        print(f"[rejected] model note cited evidence it was never shown: {rejected}", file=sys.stderr)
        print(f"[rejected] note, for the record:\n{rejected.note}", file=sys.stderr)
        print()
        print("explanation rejected by the validator; deterministic template instead:")
        print()
        print(render_template(flag))
        return 4
    return 0


def validate_cassettes(argv: list[str] | None = None) -> int:
    """`spandan validate-cassettes` - one grounding verdict per committed cassette.

    The measurement behind FAILURE_MODES §8: how many recorded explanations
    cite evidence the model was never shown. Exit 0 whatever the count; the
    number is the result, not a failure.
    """
    _utf8()
    parser = argparse.ArgumentParser(prog="spandan validate-cassettes")
    parser.add_argument("--cassettes", default=None, help="directory (default: the committed set)")
    args = parser.parse_args(argv)

    from .llm.grounding import cassette_report
    from .llm.provider import CASSETTE_DIR

    text, _rejected, total = cassette_report(Path(args.cassettes) if args.cassettes else CASSETTE_DIR)
    print(text)
    return 0 if total else 1


def triage_graph(argv: list[str] | None = None) -> int:
    """`spandan triage-graph` - the post-detection graph, from its declaration.

    `--mermaid` prints the diagram derived from the edge table, so the picture
    in the README can be diffed against the code rather than drawn beside it.
    Without flags, reports that the graph compiles and lists its nodes.
    """
    _utf8()
    parser = argparse.ArgumentParser(prog="spandan triage-graph")
    parser.add_argument("--mermaid", action="store_true", help="print the diagram derived from EDGES")
    args = parser.parse_args(argv)

    from .triage.graph import compile_graph, render_mermaid

    info = compile_graph()
    if args.mermaid:
        sys.stdout.write(render_mermaid() + "\n")
        return 0
    print(f"graph compiles: {len(info['nodes'])} nodes, start={info['start']}, end={info['end']}")
    print("  nodes: " + ", ".join(info["nodes"]))
    print("  the LLM node (explain) has one successor (ground) and no path to act or mode")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spandan", add_help=True)
    parser.add_argument(
        "command",
        choices=("replay", "explain", "validate-cassettes", "triage-graph"),
        help="replay the stream, explain one flag, validate the committed cassettes, or render the triage graph",
    )
    args, rest = parser.parse_known_args(argv)
    if args.command == "replay":
        return replay(rest)
    if args.command == "explain":
        return explain(rest)
    if args.command == "validate-cassettes":
        return validate_cassettes(rest)
    if args.command == "triage-graph":
        return triage_graph(rest)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
