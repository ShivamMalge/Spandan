# Spandan

A defense-only, deterministic streaming detector for card-testing and velocity
abuse on Indian payment streams. One detector specification, two bit-exact
engines (a Python reference and a Rust core behind PyO3), a rupee-denominated
evaluation harness that refuses to grade itself kindly, and an LLM explanation
layer that provably cannot touch a number.

Everything in this repository is synthetic: identifiers come from reserved
ranges (ISO/IEC 7812 MII-0 BINs, RFC 5737/RFC 2544 IPs, opaque non-PAN card
tokens). No real card number, IP, or merchant appears anywhere, and attack
scenarios are described only as statistical signatures, never procedures.

## The headline, before anything flattering

**Precision at a realistic merchant card-testing base rate (0.15%, assumed) is
0.0824 — roughly eleven false alarms for every true catch.** At the generator's
own 1.33% positive rate precision is 0.4462; that figure flatters the detector
by an order of magnitude, which is why it does not lead.

**A flag declines the transaction; alerts are the human-facing grouping of
those declines per (merchant, BIN), not a softer separate action.** That
semantic decides which number matters: at the headline operating point 20,254
flagged events collapse into 487 alerts (42 events per alert), so the
alerts/day budget bounds the analyst queue and says nothing about merchant
impact. The number that decides deployability is the share of **legitimate
transactions declined: 1.41%, or 1 in 71 legitimate customers.**

Against that: all 60 attack episodes in the test window are caught (event
recall 0.844), and detection is fast — p90 of 32/6/7 events to first flag for
burst/rotating/slow-low (the medians of 0–1 saturate and are not evidence;
p90 is the honest column).

The summary of where this project stands, both halves measured, neither
softened: **it detects reliably and fast, and it is not deployable as an
inline control** — because of the issuer-outage failure mode
([FAILURE_MODES §2.1](docs/FAILURE_MODES.md)) and the 1-in-71 decline rate
that follows from it.

## Headline numbers

Medians across three independently generated 100-day, 1.61M-event streams
(ranges in [FAILURE_MODES §0](docs/FAILURE_MODES.md)); threshold chosen on
validation only, under the alert-budget constraint.

| metric | value |
|---|---|
| precision @ 0.15% base rate | **0.0824** |
| event-level precision / recall | 0.4462 / 0.8444 |
| alert-level precision | 0.433 (of 487 alerts, 211 real) |
| episodes detected | 60/60 |
| legitimate transactions declined | **1.41% (1 in 71)** |
| share of all traffic flagged | 2.52% |
| net position (rupee cost model) | ₹348,845 (range ₹279k–₹395k) |
| break-even review cost | **₹613 per alert** (an output, not an input) |
| detection speed (p90 events to first flag) | burst 32 · rotating 6 · slow_low 7 |

The false-positive cost in rupees is inside the net position: the cost model
charges the full value of every legitimate transaction declined (flags block),
plus per-alert review at the analyst's loaded cost — the net stays positive
only while reviewing an alert costs under ₹613.

**Why the operating point was not moved after seeing test numbers:** the alert
budget was registered in `costs.toml` before the test window was read (commit
`e5b48f8`, 2026-08-24), and its basis — what one analyst can work through in a
day — does not depend on the results. Tighter budgets score better on test,
and moving the budget now would be selecting on the test set. The budget
frontier in `make eval` is a sensitivity analysis, not a menu.

## How to run

```
make setup     # venv + editable install + maturin build of the Rust core
make data      # generate the synthetic streams (byte-identical per seed)
make eval      # full evaluation, python reference engine
make eval ENGINE=rust    # identical numbers, rust core
make test      # pytest + cargo test
make bench     # engine benchmarks (docs/BENCH.md)
make demo      # streaming replay demo
make all       # everything above
```

`spandan explain --flag-id <id>` renders the analyst-facing explanation for a
flag (see *The explanation layer* below). A fresh clone reproduces every
number in this file: clone, create a venv **inside the clone**, `make setup &&
make all`; `git status --porcelain` prints nothing afterwards.

## Architecture

The five-minute walk, with the diagram: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Generator → temporal split → detector (per-entity sliding windows over
`(t−W, t]` on four axes, Welford/EWMA baselines folded after scoring, six
score terms in fixed order) → constrained evaluation → alerts. The LLM layer
hangs off the flag output and is unreachable from the code that produces
numbers.

**The `Axis` enum has no `Card` variant.** The card-novelty ban — this
detector must never learn "new card = suspicious", which would punish every
freshly issued card — is a compile error in the Rust core, a test failure in
Python, and a promise in `gen/ASSUMPTIONS.md §1.7a`. Three enforcement
strengths, and the type system's is the strongest.

## One spec, two engines, bit-exact

The Python reference is the specification: the `(t−W, t]` window convention,
baseline fold order, and six-term summation order were written down **before
any Rust existed**. Identical operation order over IEEE 754 doubles gives
identical results — parity risk is made of spec ambiguity, not arithmetic, and
the escape hatch going unused is evidence the paperwork was the work. The
committed 3,866-event fixture agrees at tolerance 1e-9 with measured delta
0e0 — and the first version of that fixture was still under-covering (peak
ring occupancy 58 of 512), so "bit-exact" meant "bit-exact on the happy path"
until review forced a saturating mega-burst into it.

**The real parity test is the engine swap:** `make eval ENGINE=rust` vs
`ENGINE=python` runs both cores over 1.6M events at full state depth, and the
two metrics JSONs differ in exactly one line — the engine label.

**The Rust trade, win and cost in the same sentence:** Rust buys a 5.5×
streaming throughput gain and a 4.8× better p99 (24.8µs vs 119.3µs) at twice
the memory per entity — 3,874 vs 1,975 bytes, projecting 31 GB vs 16 GB per
month at an assumed 8M distinct entities. The memory claim, stated precisely:
retained events are bounded **per entity**, but entities are never freed, so
total memory is **linear in distinct entity count** — never "bounded memory"
unqualified. The unbuilt fixes are recorded with their accuracy trades in
[FAILURE_MODES §7](docs/FAILURE_MODES.md): LRU/count-min sketch for the
growth, interning and ring right-sizing for the 2× constant — **closing a 2×
constant on an unbounded curve is polish, not the fix.** Full tables in
[docs/BENCH.md](docs/BENCH.md), including the correction history of the
benchmark itself.

## The plausible-number pattern

The recurring failure class of this project was not a bug in the detector; it
was **plausible figures with nothing behind them**, five times:

1. single-seed precision of 1.00 (a two-episode test window),
2. a "bit-exact" parity fixture that never filled a ring buffer,
3. 0.0 MB RSS from an unchecked Win32 call,
4. a batch=1 benchmark that re-scored one hot event and beat batch=100k,
5. a doc-patch script whose empty verification grep went unchecked.

**None of these were caught by staring harder at the output — each was caught
by a guard, a re-run under different conditions, or someone asking whether the
number should be true.** The mechanism, not carefulness, is the claim. The two
retractions in [FAILURE_MODES §3](docs/FAILURE_MODES.md) (the EWMA ablation,
the coarse-grid "improvement") are the same habit applied to results, and the
full history is in [docs/BUILD_LOG.md](docs/BUILD_LOG.md).

## The explanation layer

`spandan explain` replays committed cassettes by default and never touches the
network. Recording is deliberate: `SPANDAN_LLM_MODE=record` with
`GEMINI_API_KEY` set in the environment (no .env file, no dotenv loader) makes
one HTTPS call per cache miss to Gemini's OpenAI-compatible chat-completions
endpoint, model `gemini-3.1-flash-lite`; each cassette's `recorded_via` field
names the provider and exact model id that produced it. No number in the
evaluation passes through a model in either mode — `tests/test_llm.py` proves
that structurally: the full evaluation runs bit-identically with
`spandan.llm` replaced by an object that raises on any attribute access.

That boundary earned its keep. The recorded model output **fabricated
evidence** — decision rules conditioned on CVV/AVS results and per-card
history that exist nowhere in the pipeline — and misdescribed the detection
basis. The comparison against a hand-written target committed before any LLM
code existed ([TARGET.md](python/spandan/llm/TARGET.md)) concluded that a
template suffices; the deterministic template, which can only substitute
fields that exist and therefore cannot fabricate, is the shipped explainer.
The full finding, and why a hallucinating explainer here degrades one
analyst note but cannot corrupt a number, is
[FAILURE_MODES §8](docs/FAILURE_MODES.md). The cassettes stay as recorded —
re-prompting for a nicer sample would be the same error as selecting a
threshold on the test set.

Disclosure: the cassettes were recorded on the Gemini API free tier, where
Google may use prompts and responses to improve its products. Harmless here —
every identifier in the prompt is synthetic — but it belongs in the record
alongside everything else this project discloses.

## The loss class this project does not address, and its other limits

- **The single-merchant issuer outage is the headline failure**
  ([FAILURE_MODES §2.1](docs/FAILURE_MODES.md)): a negative control built to
  attack the detector's main signal without its usual crutches gets **39.9%
  of its events flagged**, and the detector's headroom over a naive
  decline-ratio rule is negative on that control. That failure, not the alert
  queue, is what makes the 1-in-71 decline rate irreducible at this design's
  window size — and the diagnosed-but-unbuilt fixes (long-horizon BIN window,
  joint flag-rate constraint) are recorded in §7, not quietly attempted on a
  frozen detector.
- The 5-minute window cannot see retry separation (§2.2) or anything slower
  than it (a patient tester spacing probes hours apart defeats it, §5).
- Memory grows linearly with distinct entities; see the trade sentence above.
- All numbers are synthetic-stream numbers. The generator's assumptions and
  their known divergences from production traffic are itemized in
  [gen/ASSUMPTIONS.md](python/spandan/gen/ASSUMPTIONS.md).

## Naming

Checked 2026-08-26: `spandan` is unclaimed on PyPI
(`pypi.org/pypi/spandan/json` → 404) and on crates.io, and `spandan-core` —
the crate's actual name in `Cargo.toml` — is unclaimed on crates.io too (all
three checks returned 404). Nothing is published; the check is recorded so the
packaging names are known to be claimable.

---

Build plan and phase gates: [docs/PHASES.md](docs/PHASES.md) · failure modes:
[docs/FAILURE_MODES.md](docs/FAILURE_MODES.md) · benchmarks:
[docs/BENCH.md](docs/BENCH.md) · build log: [docs/BUILD_LOG.md](docs/BUILD_LOG.md)
· house rules: [agents.md](agents.md)
