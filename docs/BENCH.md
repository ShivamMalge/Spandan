# Engine benchmarks

Rust core (`spandan-core` behind PyO3) against the Python reference
(`detect/reference.py`), measured by `make bench` on the build machine (Windows
11, Python 3.11.9, `--release` wheel). Raw output; nothing here was re-run until
it looked better, and the losses are in the tables.

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

200,000 real events; per-row median of repeated calls on a warmed detector.

| batch | engine | events/s | µs/event |
|---|---|---|---|
| 1 | python | 35,026 | 28.55 |
| 1 | **rust** | 52,910 | 18.90 |
| 10 | python | 39,262 | 25.47 |
| 10 | **rust** | 152,788 | 6.54 |
| 100 | python | 37,623 | 26.58 |
| 100 | **rust** | 179,872 | 5.56 |
| 1,000 | python | 39,655 | 25.22 |
| 1,000 | **rust** | 180,138 | 5.55 |
| 10,000 | python | 36,895 | 27.10 |
| 10,000 | **rust** | 162,389 | 6.16 |
| 100,000 | python | 38,857 | 25.74 |
| 100,000 | **rust** | 157,952 | 6.33 |

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

**Rust wins every batch size measured, including batch=1.** The plan required at
least one row where the reference wins or ties; that row did not materialise on
this machine, and saying so honestly is the requirement — the closest regime is
batch=1, where per-call overhead (kwargs, columnarisation of a single event,
FFI) eats most of Rust's advantage.

"Zero-copy" in these numbers means the **numeric columns** (timestamps, amounts,
declined flags) are borrowed from NumPy without copying. The six identifier
columns are Python strings and are converted — one allocation per string — and
that cost is inside every Rust row above, not excused out of it.

## 3. Streaming (`update` per event) — the live-integration shape

| engine | events/s | p50 µs | p95 µs | p99 µs |
|---|---|---|---|---|
| python | 21,766 | 43.80 | 72.80 | 119.30 |
| **rust** | 120,053 | **8.10** | 11.50 | 24.80 |

5.5× on throughput, and the p99 stays under 25µs. This is the shape a live
authorization hook sees, and it is where the Rust core earns its place: at
120k events/s a single thread covers roughly 10 billion events/day of headroom.

## 4. Memory — where Rust loses, and the claim stated precisely

**The memory claim, stated precisely: retained events are bounded per entity
(the 512-slot ring), but entities are never freed, so total memory is LINEAR in
distinct entity count.** The test formerly named `memory_bounded_under_entity_churn`
asserted the per-entity bound only, and has been renamed
`window_memory_bounded_per_entity` to say what it checks.

Full stream (200k events, 10,819 entities): python +5.6MB, rust +58.6MB RSS.

High-cardinality churn — nearly every event brings a never-seen IP and device,
the worst realistic case (carrier-grade NAT churns addresses aggressively):

| engine | events | entities | RSS grown | bytes/entity | projected monthly |
|---|---|---|---|---|---|
| rust | 400,000 | 531,282 | 2,058 MB | **3,874** | **31.0 GB** |
| python | 400,000 | 531,282 | 1,049 MB | **1,975** | **15.8 GB** |

**The Rust engine costs ~2× the memory per entity of the Python one.** Diagnosed
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

**The trade in one sentence: Rust buys a 5.5× streaming throughput gain and a
4.8× better p99 (24.8µs vs 119.3µs) at twice the memory per entity (3,874 vs
1,975 bytes, projecting 31 GB vs 16 GB per month at 8M entities).** Both halves
are measured; neither is the whole story alone.

- **Rust**: every throughput regime measured, most decisively streaming (5.5×,
  p99 < 25µs) and mid-size batches (4–5×). The case for the port is latency and
  headroom, and it is real.
- **Python reference**: memory (½ the per-entity cost), inspectability (rich
  `Flag` evidence on every update; the Rust surface returns scores), and being
  the specification. `make eval` runs it by default for exactly that reason.
- **Neither**: the evaluation's wall clock, which is dominated by generation and
  the threshold sweep — engine choice moves `make eval` by ~2×, not 4–5×.
