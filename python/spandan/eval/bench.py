"""`make bench` — honest engine benchmarks, including the losses.

Three sections, per PHASES.md Phase 4 plus the Phase 3 gate's memory addition:

1. **Throughput and latency** across batch sizes chosen to span the regime where
   the Python reference is competitive *and* the regime where it is not. The
   per-call FFI and columnarisation overhead means small batches and the
   streaming path are where Rust can lose; the table includes them rather than
   starting at the batch size that flatters Rust.
2. **Peak RSS** for a full-stream scoring pass, per engine.
3. **High-cardinality churn**: a stream where nearly every event brings a
   never-seen IP and device. Entities are never freed, so total memory is
   LINEAR in distinct entity count — this measures the slope (bytes per entity)
   and projects it at a realistic monthly cardinality, because "bounded memory"
   without that number is a claim a systems reviewer catches instantly.

Nothing here is optimised in response to its own output (out of scope by
decision); slow rows are recorded, not chased.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import gc
import statistics
import sys
import time
from pathlib import Path

from ..detect import DetectorConfig
from ..detect.rust_engine import make_detector
from ..gen.build import TRAIN_FILENAME, read_stream
from ..gen.schema import STATUS_APPROVED, STATUS_DECLINED, Event

#: Chosen to span both regimes: the head of the table is where per-call overhead
#: dominates (Python can win), the tail is where the core dominates.
BATCH_SIZES = (1, 10, 100, 1_000, 10_000, 100_000)

#: Assumed distinct-entity cardinality for one month of real merchant traffic,
#: for the memory projection. ASSUMPTION, stated as one: a mid-size gateway
#: portfolio sees millions of distinct IPs a month (mobile carrier NAT churns
#: addresses aggressively in India); 5M distinct IPs plus 3M devices plus the
#: small BIN/merchant axes is a round, defensible order of magnitude, and the
#: projection is linear so any other assumption is one multiplication away.
PROJECTED_MONTHLY_ENTITIES = 8_000_000


class _MemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("PageFaultCount", ctypes.wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _memory_counters() -> _MemoryCounters:
    """Process memory counters via Win32, no new dependency.

    Modern Windows exports this as `K32GetProcessMemoryInfo` on kernel32; the
    old psapi name resolves on some systems and silently fails on others, which
    is exactly what happened on the first run of this benchmark - every RSS
    column read 0.0MB and the numbers looked plausible enough to almost ship.
    The return value is checked now, so a failure raises instead of reporting a
    memory measurement that measured nothing.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    info = kernel32.K32GetProcessMemoryInfo
    # Without declared types, ctypes passes the process pseudo-handle through a
    # default c_int, which truncates it on 64-bit and the call fails. That is
    # the failure the ok-check below caught on this benchmark's second run.
    info.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(_MemoryCounters),
        ctypes.wintypes.DWORD,
    ]
    info.restype = ctypes.wintypes.BOOL
    current = kernel32.GetCurrentProcess
    current.restype = ctypes.wintypes.HANDLE

    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not info(current(), ctypes.byref(counters), counters.cb):
        raise OSError(
            f"K32GetProcessMemoryInfo failed (err {ctypes.get_last_error()}); "
            "RSS figures would be fiction"
        )
    return counters


def _peak_rss_bytes() -> int:
    return _memory_counters().PeakWorkingSetSize


def _current_rss_bytes() -> int:
    return _memory_counters().WorkingSetSize


# --- throughput and latency ---------------------------------------------------


def bench_batches(events: list[Event]) -> list[dict]:
    rows = []
    for size in BATCH_SIZES:
        if size > len(events):
            continue
        for engine in ("python", "rust"):
            detector = make_detector(engine, DetectorConfig())
            # Warm state so we measure steady-state scoring, not dict growth.
            detector.score_batch(events[: min(50_000, len(events))])

            chunk = events[50_000 : 50_000 + size] if len(events) > 50_000 + size else events[:size]
            # Enough repetitions for a stable clock reading at small sizes.
            repeats = max(3, min(400, 20_000 // size))
            timings = []
            for _ in range(repeats):
                t0 = time.perf_counter()
                detector.score_batch(chunk)
                timings.append(time.perf_counter() - t0)

            per_call = statistics.median(timings)
            rows.append(
                {
                    "engine": engine,
                    "batch": size,
                    "events_per_sec": size / per_call,
                    "per_event_us": per_call / size * 1e6,
                    "calls_timed": repeats,
                }
            )
    return rows


def bench_streaming(events: list[Event], count: int = 20_000) -> list[dict]:
    """The `update` path, one event per call — the live-integration shape."""
    rows = []
    for engine in ("python", "rust"):
        detector = make_detector(engine, DetectorConfig())
        detector.score_batch(events[:50_000])

        chunk = events[50_000 : 50_000 + count]
        latencies = []
        for event in chunk:
            t0 = time.perf_counter()
            detector.update(event)
            latencies.append(time.perf_counter() - t0)

        latencies.sort()
        n = len(latencies)
        rows.append(
            {
                "engine": engine,
                "events": n,
                "events_per_sec": n / sum(latencies),
                "p50_us": latencies[n // 2] * 1e6,
                "p95_us": latencies[int(n * 0.95)] * 1e6,
                "p99_us": latencies[int(n * 0.99)] * 1e6,
            }
        )
    return rows


# --- memory -------------------------------------------------------------------


def bench_churn(engine: str, event_count: int = 400_000) -> dict:
    """Nearly every event brings a new IP and device: the worst realistic case.

    Entities are never freed, so this measures the growth slope directly. Events
    are synthesised in a generator so the event objects themselves do not sit in
    a list inflating the RSS reading.
    """
    detector = make_detector(engine, DetectorConfig())
    gc.collect()
    rss_before = _current_rss_bytes()

    batch: list[Event] = []
    for i in range(event_count):
        batch.append(
            Event(
                ts=i * 250,
                txn_id=f"txn_{i:09d}",
                merchant_id=f"mer_{i % 10:03d}",
                bin=f"0{i % 200:05d}",
                card_ref=f"card_{i:010d}",
                ip=f"198.{18 + (i >> 16) % 2}.{(i >> 8) & 255}.{i & 255}",
                device_id=f"dev_{i:010d}",
                amount_paise=10_000 + (i % 900),
                status=STATUS_DECLINED if i % 11 == 0 else STATUS_APPROVED,
                label=0,
                scenario_id="bench",
            )
        )
        if len(batch) == 20_000:
            detector.score_batch(batch)
            batch.clear()
    if batch:
        detector.score_batch(batch)
        batch.clear()

    gc.collect()
    rss_after = _current_rss_bytes()
    entities = detector.entity_count() if hasattr(detector, "entity_count") else len(
        detector._state
    )
    grown = rss_after - rss_before
    per_entity = grown / entities if entities else 0.0
    return {
        "engine": engine,
        "events": event_count,
        "entities": entities,
        "rss_grown_mb": grown / 1e6,
        "bytes_per_entity": per_entity,
        "projected_monthly_gb": per_entity * PROJECTED_MONTHLY_ENTITIES / 1e9,
    }


def bench_full_stream_rss(events: list[Event]) -> list[dict]:
    """Peak RSS is process-wide and monotone, so per-engine numbers need
    separate processes; within one process we report RSS growth instead."""
    rows = []
    for engine in ("python", "rust"):
        detector = make_detector(engine, DetectorConfig())
        gc.collect()
        before = _current_rss_bytes()
        detector.score_batch(events)
        gc.collect()
        after = _current_rss_bytes()
        entities = (
            detector.entity_count()
            if hasattr(detector, "entity_count")
            else len(detector._state)
        )
        rows.append(
            {
                "engine": engine,
                "events": len(events),
                "entities": entities,
                "rss_grown_mb": (after - before) / 1e6,
            }
        )
        del detector
        gc.collect()
    return rows


# --- report -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    for stream_handle in (sys.stdout, sys.stderr):
        try:
            stream_handle.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="Benchmark the two engines honestly.")
    parser.add_argument("--data", default="data")
    parser.add_argument("--limit", type=int, default=300_000)
    args = parser.parse_args(argv)

    events = read_stream(Path(args.data) / TRAIN_FILENAME)[: args.limit]
    print(f"benchmark stream: {len(events):,} events from {args.data}/{TRAIN_FILENAME}")
    print(f"peak RSS at start: {_peak_rss_bytes()/1e6:,.0f} MB")
    print()

    print("=" * 78)
    print("BATCH SCORING - both regimes, including where Rust loses")
    print("=" * 78)
    rows = bench_batches(events)
    print(f"{'batch':>9}{'engine':>9}{'events/s':>14}{'us/event':>11}{'calls':>7}")
    winners = {}
    for row in rows:
        print(
            f"{row['batch']:>9,}{row['engine']:>9}{row['events_per_sec']:>14,.0f}"
            f"{row['per_event_us']:>11.2f}{row['calls_timed']:>7}"
        )
        best = winners.get(row["batch"])
        if best is None or row["events_per_sec"] > best[1]:
            winners[row["batch"]] = (row["engine"], row["events_per_sec"])
    print()
    python_wins = [size for size, (engine, _) in sorted(winners.items()) if engine == "python"]
    if python_wins:
        print(f"python wins or ties at batch size(s): {python_wins} - the per-call FFI and")
        print("columnarisation overhead dominates below the crossover, and a deployment")
        print("that scores one event per call should read those rows, not the big ones.")
    else:
        print("rust wins at every batch size measured, including batch=1. The honest row")
        print("the plan asked for (a NumPy win) did not materialise on this machine; the")
        print("closest regime is the streaming path below.")

    print()
    print("=" * 78)
    print("STREAMING (update per event) - the live-integration shape")
    print("=" * 78)
    stream_rows = bench_streaming(events)
    print(f"{'engine':>9}{'events/s':>14}{'p50 us':>10}{'p95 us':>10}{'p99 us':>10}")
    for row in stream_rows:
        print(
            f"{row['engine']:>9}{row['events_per_sec']:>14,.0f}{row['p50_us']:>10.2f}"
            f"{row['p95_us']:>10.2f}{row['p99_us']:>10.2f}"
        )

    print()
    print("=" * 78)
    print("MEMORY - full stream")
    print("=" * 78)
    rss_rows = bench_full_stream_rss(events)
    print(f"{'engine':>9}{'events':>10}{'entities':>10}{'RSS grown':>12}")
    for row in rss_rows:
        print(
            f"{row['engine']:>9}{row['events']:>10,}{row['entities']:>10,}"
            f"{row['rss_grown_mb']:>10.1f}MB"
        )

    print()
    print("=" * 78)
    print("MEMORY - high-cardinality churn (nearly every event a new IP + device)")
    print("=" * 78)
    print("entities are NEVER FREED: total memory is linear in distinct entity count.")
    print("this measures the slope. 'bounded memory' means bounded PER ENTITY only.")
    print()
    churn_rows = [bench_churn("rust"), bench_churn("python")]
    print(f"{'engine':>9}{'events':>10}{'entities':>11}{'RSS grown':>12}{'bytes/entity':>14}"
          f"{'proj. monthly':>15}")
    for row in churn_rows:
        print(
            f"{row['engine']:>9}{row['events']:>10,}{row['entities']:>11,}"
            f"{row['rss_grown_mb']:>10.1f}MB{row['bytes_per_entity']:>14,.0f}"
            f"{row['projected_monthly_gb']:>13.1f}GB"
        )
    print()
    print(f"projection basis: {PROJECTED_MONTHLY_ENTITIES:,} distinct entities/month")
    print("(ASSUMPTION - see bench.py docstring; the projection is linear, so any other")
    print("cardinality is one multiplication away). The unbuilt fix - LRU eviction or a")
    print("count-min sketch, each trading accuracy for a hard cap - is FAILURE_MODES 7.")

    print()
    print(f"peak RSS at end: {_peak_rss_bytes()/1e6:,.0f} MB (process-wide, both engines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
