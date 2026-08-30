# Engine benchmarks

Rust core (`spandan-core` behind PyO3) against the Python reference
(`detect/reference.py`). Raw output; nothing here was re-run until it looked
better, and the losses are in the tables.

**Every figure below is one `make bench` run, 2026-08-30, on: AMD Ryzen 7 5800HS
(16 logical cores), 15.4 GB RAM, Windows 11 build 10.0.26200, Python 3.11.9,
rustc 1.94.0, `--release` wheel, 300,000-event stream.** These are timings and a
process-RSS measurement on one loaded laptop; they are the least reproducible
numbers in this repository and they are not expected to reproduce exactly
anywhere else. See §6 for how far they moved between two runs on this same
machine.

The reference is the *specification* — scalar, loop-shaped, written to be read.
It was not de-optimised to lose: its O(1) incremental aggregates were added in
Phase 2 for its own sake, before any Rust existed.

---

## 1. The result that matters most: the engines agree

`make eval ENGINE=python` and `make eval ENGINE=rust` over the full 1.61M-event
stream — threshold selection, frontier, ablations, three-seed matrix, every
metric — produce metrics JSONs whose **only differing line is the engine label**:

```
11c11
<   "engine": "python",
>   "engine": "rust",
diff exit=1
```

Wall time: 12m15s (python) vs 6m47s (rust); most of both is stream generation
and the sweep, which are engine-independent.

This is the real parity test, and it was expected to be able to fail: the Phase 3
fixture was bit-exact but is 3,866 events, and this run is 1.6M events at full
state depth across three generated streams and three detector variants. A
divergence here would have meant the fixture was still under-covering. There was
none.

## 2. Batch scoring

300,000 real events; per-row median of repeated calls on a warmed detector.

| batch | engine | events/s | µs/event |
|---|---|---|---|
| 1 | python | 33,956 | 29.45 |
| 1 | **rust** | 52,632 | 19.00 |
| 10 | python | 40,000 | 25.00 |
| 10 | **rust** | 149,254 | 6.70 |
| 100 | python | 39,120 | 25.56 |
| 100 | **rust** | 179,388 | 5.57 |
| 1,000 | python | 40,723 | 24.56 |
| 1,000 | **rust** | 184,250 | 5.43 |
| 10,000 | python | 38,549 | 25.94 |
| 10,000 | **rust** | 165,157 | 6.05 |
| 100,000 | python | 37,858 | 26.41 |
| 100,000 | **rust** | 152,063 | 6.58 |

**These are corrected figures; the first published table was wrong in both
directions.** The Phase 4 gate asked whether 40,816 ev/s of per-event Python was
too good to be true, and it was: the original benchmark re-scored the *same*
chunk every repetition, so batch=1 measured re-scoring one hot event — one warm
entity, cache-resident dicts, nothing evicting — and under-measured the Python
baseline by ~2× (13.8µs same-chunk vs 29.8µs fresh events, verified directly).
The tell was in the table itself: batch=1 came out faster per event than
batch=100k, and the streaming bench, which walks fresh events, disagreed. The
same flaw *inflated* Rust's mid-size rows: repeated chunks stacked duplicate
events into windows and made them artificially long (8.6µs → 5.6µs corrected).
Each repetition now walks a fresh, disjoint chunk.

The correction cuts against Rust at batch=1 in relative terms (1.5× rather than
1.6×) and for it at mid sizes (4–5× rather than 3–4×); it was made because the
number was wrong, not because of which way it pointed.

**Rust wins every batch size measured, including batch=1** (1.55× there, 4.5–4.6×
at mid sizes). The plan required at least one row where the reference wins or
ties; that row did not materialise on this machine, and saying so is the
requirement — the closest regime is batch=1, where per-call overhead (kwargs,
columnarisation of a single event, FFI) eats most of Rust's advantage.

"Zero-copy" in these numbers means the **numeric columns** (timestamps, amounts,
declined flags) are borrowed from NumPy without copying. The six identifier
columns are Python strings and are converted — one allocation per string — and
that cost is inside every Rust row above, not excused out of it.

## 3. Streaming (`update` per event) — the live-integration shape

| engine | events/s | p50 µs | p95 µs | p99 µs |
|---|---|---|---|---|
| python | 37,191 | 25.50 | 37.50 | 52.40 |
| **rust** | 199,925 | **4.70** | 6.20 | 11.60 |

**5.38× on throughput and 4.52× on p99**, which stays under 12µs. This is the
shape a live authorization hook sees, and it is where the Rust core earns its
place: at 200k events/s a single thread covers roughly 17 billion events/day of
headroom. Single-threaded; no concurrent measurement was made.

## 4. Memory — where Rust loses, and the claim stated precisely

**The memory claim, stated precisely: retained events are bounded per entity
(the 512-slot ring), but entities are never freed, so total memory is LINEAR in
distinct entity count.** The test formerly named `memory_bounded_under_entity_churn`
asserted the per-entity bound only, and has been renamed
`window_memory_bounded_per_entity` to say what it checks.

Full stream (300k events, 12,662 entities): python +10.3MB, rust +65.8MB RSS.

High-cardinality churn — nearly every event brings a never-seen IP and device,
the worst realistic case (carrier-grade NAT churns addresses aggressively):

| engine | events | entities | RSS grown | bytes/entity | projected monthly |
|---|---|---|---|---|---|
| rust | 400,000 | 531,282 | 2,560 MB | **4,819** | **38.6 GB** |
| python | 400,000 | 531,282 | 1,047 MB | **1,971** | **15.8 GB** |

**The Rust engine costs 2.44× the memory per entity of the Python one.** Diagnosed
but deliberately not fixed (Phase 4 rule: record what the benchmark exposes, do
not chase it; and the core is frozen through this phase):

- `Window::new` pre-allocates 64 ring slots per entity; a churn entity that only
  ever sees one event still pays for 64 slots of ~72 bytes.
- Each retained slot owns two heap `String`s (card, merchant); Python's tuples
  share references to existing string objects.

The projection basis is an assumption, stated as one: 8M distinct entities/month
(order-of-magnitude for a mid-size gateway portfolio under mobile NAT). The
growth is linear, so any other cardinality is one multiplication away. Same-
process sequential RSS measurement is approximate; the churn slope is the robust
number, the full-stream deltas less so.

**Consequences:** at real merchant cardinalities this detector needs entity
eviction (LRU) or a sketch (count-min for the velocity counts) to run
indefinitely — either trades accuracy for a hard memory cap, neither was built,
and the trade is recorded in `docs/FAILURE_MODES.md` §7. Until one exists, the
honest deployment statement is "restart or shard before the entity table exceeds
memory", and 31 GB/month says how often that is.

## 5. Which regime favours which

**The trade in one sentence: Rust buys a 5.38× streaming throughput gain and a
4.52× better p99 (11.6µs vs 52.4µs) at 2.44× the memory per entity (4,819 vs
1,971 bytes, projecting 38.6 GB vs 15.8 GB per month at an assumed 8M
entities).** Both halves are measured; neither is the whole story alone.

- **Rust**: every throughput regime measured, most decisively streaming (5.38×,
  p99 < 12µs) and mid-size batches (4.5–4.6×). The case for the port is latency
  and headroom, and it is real.
- **Python reference**: memory (½ the per-entity cost), inspectability (rich
  `Flag` evidence on every update; the Rust surface returns scores), and being
  the specification. `make eval` runs it by default for exactly that reason.
- **Neither**: the evaluation's wall clock, which is dominated by generation and
  the threshold sweep — engine choice moves `make eval` by ~2×, not 4–5×.

## 6. These figures moved between two runs on the same machine

The tables above are the 2026-08-30 run. The previous published run, same
machine, same commit of the detector, gave materially different numbers, and the
difference is recorded here rather than smoothed over:

| figure | earlier run | 2026-08-30 run | change |
|---|---|---|---|
| rust streaming | 120,053 ev/s, p99 24.80µs | 199,925 ev/s, p99 11.60µs | +66% / −53% |
| python streaming | 21,766 ev/s, p99 119.30µs | 37,191 ev/s, p99 52.40µs | +71% / −56% |
| rust bytes/entity | 3,874 | 4,819 | +24% |
| python bytes/entity | 1,975 | 1,971 | −0.2% |
| rust churn RSS | 2,058 MB | 2,560 MB | +24% |
| batch=1 python | 28.55 µs | 29.45 µs | +3% |

**The throughput and latency movement is machine state**, not a code change:
background load, thermal headroom and clock behaviour on a laptop move timings
by this much, and the batch table — which is dominated by steady-state work —
barely moved.

**The memory movement is not explained that way, and should not be presented as
though it were.** The churn workload is deterministic: both runs saw exactly
531,282 entities. Python's slope is stable to 0.2% across the two runs while
Rust's moved 24%, which points at allocator behaviour — arena reuse and
fragmentation under a different heap history — rather than at measurement noise.
Same-process sequential RSS sampling cannot separate those. **The honest
statement is that the Rust per-entity slope is somewhere around 3,900–4,800
bytes and this benchmark cannot pin it more tightly than that.** The conclusion
the number is used for is unaffected in either direction: the slope is linear in
entity count, it is roughly 2–2.5× Python's, and the deployment blocker is the
linearity rather than the constant.

What would pin it: an allocator-level counter rather than process RSS, and a
fresh process per engine. Neither was built.
