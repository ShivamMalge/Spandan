# Spandan

[![ci](https://github.com/ShivamMalge/Spandan/actions/workflows/ci.yml/badge.svg)](https://github.com/ShivamMalge/Spandan/actions/workflows/ci.yml)

A deterministic streaming detector for card-testing and velocity abuse on card
authorization traffic, with a Rust core, a bit-exact Python reference, and an
evaluation harness that prices its own false positives in rupees.

**In fifteen seconds.** Precision **0.0824** at a realistic 0.15% base rate —
eleven false alarms per catch. As an inline control it declines **1 in 71**
legitimate customers, which is not deployable. The configuration this project
would ship is alert-only at budget 2: 2.1 alerts a day, 35 of 60 attack episodes
surfaced, 1 in 271 flagged. The one measured failure is a single-merchant issuer
outage, 50.5% of which is flagged as card testing; a kill-switch in the
post-detection graph recovers 31% of those false declines (1 in 71 → 1 in 102)
without touching a score, and the detector-level fix stays unbuilt. Every figure
here reproduces from `make eval` on a fresh clone.

## The problem

Stolen card numbers are worthless until someone knows which ones still work. The
cheapest way to find out is to attempt a small authorization and read the response,
so a merchant's checkout gets used as a liveness oracle: a run of low-value
attempts, mostly declined, concentrated on a few issuing BINs, arriving faster than
that merchant's real customers. No single attempt is anomalous — the rate and the
concentration are. Merchants pay for this in scheme chargeback ratios, in
authorization-abuse penalties, and in the downstream fraud on whichever cards came
back approved.

Spandan scores each authorization attempt against per-entity baselines it learns
from the stream, and **a flag declines the transaction** — it is an inline
authorization control, not a notification. Alerts are the human-facing grouping of
those declines per (merchant, BIN), not a softer separate action.

## Against the Track 2 bar

The track asks for a detector for one class of loss, with measured precision and
recall on a held-out test set, honest metrics including false-positive cost, and
strictly defense-only. Where each clause is met, and what proves it:

| clause | where | proof |
|---|---|---|
| one class of loss | card testing and velocity abuse; three attack signatures, three negative controls ([ASSUMPTIONS.md](python/spandan/gen/ASSUMPTIONS.md) §1.7) | `make eval` per-scenario table |
| held-out test set | temporal split, day-50 wall; every tunable chosen on validation; alert budget registered before test was read (`e5b48f8`) | `test_loader_rejects_nontemporal_split`, `test_threshold_selection_never_touches_test_window` |
| measured precision and recall | three precisions, the least flattering leading; recall 0.8444; 60/60 episodes | `make eval`; two runs four days apart diff identical |
| false-positive cost | rupee model with every assumption labelled; break-even ₹613/alert as an output; 1 in 71 legitimate customers declined | [`costs.toml`](python/spandan/eval/costs.toml); `make eval` |
| defense-only | reserved-range identifiers, no card number anywhere, scenarios as statistical signatures only | `test_all_identifiers_synthetic`, `test_no_card_reference_could_be_mistaken_for_a_pan` |
| show your work | this file, [ARCHITECTURE.md](docs/ARCHITECTURE.md), [BUILD_LOG.md](docs/BUILD_LOG.md) | fresh clone: `make setup && make all`, `git status --porcelain` empty |

Four evaluation criteria are reported for this buildathon by secondary
coverage — not on the official page, so treated as reported. Where each lives:
**Problem taste** — the loss class and the rupee model, above. **Build quality**
— 125 Python and 33 Rust tests, two engines bit-exact over 1.6M events,
reproduction from a fresh clone. **AI judgment** — the language model is
structurally unable to reach a number and its notes are validated; see *Where
AI is*. **Failure recovery** — [BUILD_LOG.md](docs/BUILD_LOG.md): thirteen entries,
each with the wrong diagnosis written before the right one;
`spandan replay --cold-start` demonstrates a failure mode live; and the triage
graph's kill-switch is a graceful degradation aimed at the measured worst
failure, with its effect measured rather than described.

## Results

Median across three independently generated 100-day streams (1.61M events, 240
attack and control episodes). Threshold selected on validation only, under an
alerts/day budget fixed before the test window was read. Ranges in
[docs/FAILURE_MODES.md](docs/FAILURE_MODES.md) §0.

| metric | value |
|---|---|
| **precision @ 0.15% base rate** | **0.0824** — eleven false alarms per true catch |
| **legitimate transactions declined** | **1.41%, or 1 in 71** |
| legitimate declined, with the triage kill-switch | 0.98%, or 1 in 102 — routing, not detection |
| precision @ generator's 1.33% rate | 0.4462 |
| alert-level precision | 0.433 (211 real of 487 alerts) |
| recall (event-level) | 0.8444 |
| episodes detected | 60/60 |
| events to first flag (p90) | burst 32 · rotating 6 · slow_low 7 |
| PR-AUC | 0.6615 |
| share of all traffic flagged | 2.52% |
| net position, rupee cost model | ₹348,845 (range ₹279,151–₹395,007) |
| break-even review cost | ₹613 per alert |
| streaming throughput / p99 (Rust) | 199,925 events/s / 11.6µs |

Precision at the 0.15% base rate leads because the generator's own positive rate is
about ten times a realistic merchant rate, and quoting 0.4462 flatters the detector
by an order of magnitude. Recall is prevalence-independent; precision is not.

The false-positive cost is inside the net position: the model charges contribution
margin on every legitimate transaction declined, plus per-alert review, and the net
stays positive only while an alert review costs under ₹613.

**Against learned baselines on the same features** (median of three seeds; same
alert budget, same pipeline; [FAILURE_MODES.md](docs/FAILURE_MODES.md) §9):

| model | precision @ 0.15% | recall | net ₹ |
|---|---|---|---|
| hand weights, the detector | 0.0824 | 0.8444 | 348,845 |
| logistic regression, the same six terms | 0.0845 | 0.9228 | 632,567 |
| gradient boosting, nine features | 0.0699 | 0.9651 | 705,752 |

Learning the weights does not move precision at the realistic base rate. It buys
recall at the same alert budget, and neither learned model fixes the single-merchant
outage: the linear one still flags 45% of it and the boosted one 70%, against
50.5%. The learned models are reported, not shipped; `make baselines` reproduces them.

**Two results, and they are separate claims.** It detects every attack episode, and
fast — 60/60, p90 of 32 events on burst episodes of 190–300. And at a realistic base
rate it declines 1 in 71 legitimate customers, which is not deployable as an inline
control — for scale, Datos Insights puts the industry-wide e-commerce false-decline
rate at 1.51% of sales (2024), so this detector alone would add about that much
again. The reason is one measured failure, below.

**What this project would ship.** Inline blocking is not deployable at any alert
budget. The configuration it would ship is **alert-only at budget 2**: event
precision 0.696, precision 0.2034 at a 0.15% base rate, 2.1 alerts/day, 35 of 60
episodes caught, and 0.37% of legitimate traffic flagged — **1 in 271** rather than
1 in 71. One analyst reviewing two items a day surfaces 58% of campaigns. The
caveat travels with it: with flags notifying rather than declining, nothing is
prevented until a human acts, this project has no response-time model, and so
neither the blocked-good cost nor the avoided-chargeback saving can be claimed —
the ₹348,845 net is an inline-blocking figure and does not transfer. Full frontier
in [FAILURE_MODES.md](docs/FAILURE_MODES.md) §0.1.

## Run it

Requires Python ≥3.10 and a Rust toolchain.

```
git clone <repo> spandan && cd spandan
python -m venv .venv && . .venv/Scripts/activate    # Windows; use bin/activate on POSIX
make setup                                          # pip install -e .[dev] + maturin build
make data                                           # generate streams        ~2 min
make eval                                           # full evaluation         ~14 min
make check                                          # every documented figure against that run
```

`make eval ENGINE=rust` runs the same evaluation through the Rust core and produces
a byte-identical metrics JSON. `make test` runs 125 Python tests (~13 min, several
build streams and run full evaluations) and `cargo test` runs 33 Rust tests.
`make bench` reproduces [docs/BENCH.md](docs/BENCH.md). `make all` chains data,
test, eval, demo and check. CI runs the test suite in one job and `make data`,
`make eval` and `make check` in a parallel job on ubuntu, so every push regenerates
the stream and the evaluation from nothing and fails if any documented figure moves.

```
spandan replay --data data --limit 20000     # streaming demo with rupee exposure
spandan replay --cold-start                  # the cold-start failure, deliberately
spandan explain --flag-id <txn_id>           # analyst-facing explanation for one flag
spandan validate-cassettes                   # grounding verdict per recorded explanation
```

`spandan` is a console script installed by `make setup`; if a shell cannot find it
(a stale install, or `Scripts/` not on `PATH`), every command works as
`python -m spandan.cli <command> ...` with no other change.

Every figure in the results table above except the throughput row is reproduced
exactly by `make eval` on a fresh clone — two runs four days apart diff
identically. The throughput, latency and memory figures come from `make bench`,
which is a single timed run on one machine; they will not reproduce exactly, and
[docs/BENCH.md](docs/BENCH.md) §6 records how far they moved between two runs
here. After `make all`, `git status --porcelain` prints nothing.

## Use it

Three things to know. A detector is a state machine you feed events into, in time
order. `update()` returns a `Flag` when the event scores above the threshold and
`None` otherwise. **Baselines are learned from the stream, so a cold detector
scores nothing useful** — it needs history before its output means anything, which
is why the snippet below replays the training window first.

```python
from pathlib import Path
from spandan.detect import DetectorConfig, ReferenceDetector
from spandan.gen.build import read_stream, TRAIN_FILENAME, TEST_FILENAME

detector = ReferenceDetector(DetectorConfig(threshold=21.99))

# Warm the per-entity baselines. Skip this and see docs/FAILURE_MODES.md §2.3.
for event in read_stream(Path("data") / TRAIN_FILENAME):
    detector.update(event)

for event in read_stream(Path("data") / TEST_FILENAME):
    flag = detector.update(event)
    if flag is None:
        continue
    print(f"FLAG {flag.txn_id}  {flag.merchant_id}  BIN {flag.bin}")
    print(f"  score {flag.score:.2f} > threshold {flag.threshold:.2f}")
    print(f"  window: {flag.window_events} events, {flag.window_decline_ratio:.0%} declined"
          f" (this BIN's baseline {flag.baseline_decline_ratio:.0%})")
    for term, value in flag.contributions:
        print(f"    {term:<16}{value:+8.3f}")
    break
```

```
FLAG txn_000804993  mer_008  BIN 099813
  score 24.28 > threshold 21.99
  window: 1 events, 100% declined (this BIN's baseline 10%)
    decline_bin      +18.029
    amount            +6.253
    velocity_bin      +0.000
    velocity_ip       +0.000
    repetition        -0.000
    merchant_span     -0.000
```

The `Flag` is frozen and carries the evidence the score was computed from, so
nothing downstream has to recompute anything. **The six contributions sum to the
score** (`test_flag_contributions_sum_to_the_score`), which is what stops an
explanation asserting a cause the arithmetic does not support.

**Swapping engines** is one string, and it is how the parity claim is checked:

```python
from spandan.detect.rust_engine import make_detector

scores = {e: make_detector(e, config).score_batch(events) for e in ("python", "rust")}
np.array_equal(scores["python"], scores["rust"])   # True
np.max(np.abs(scores["python"] - scores["rust"]))  # 0.0  — on 50,000 real events
```

**To feed your own traffic**, build `spandan.gen.schema.Event` records: `ts`
(epoch ms), `txn_id`, `merchant_id`, `bin`, `card_ref` (any opaque token — never a
card number), `ip`, `device_id`, `amount_paise`, and `status` (`"approved"` or
`"declined"`). `label` and `scenario_id` exist for evaluation only; the detector
cannot read them, and `FEATURE_COLUMNS` excludes both. Events must arrive in
non-decreasing `ts` order — the window logic assumes it.

## Architecture

```mermaid
%%{init: {"flowchart": {"wrappingWidth": 340, "nodeSpacing": 50, "rankSpacing": 55, "curve": "basis", "padding": 12}}}%%
flowchart TB
    GB["<b>benign traffic</b><br/>Poisson · diurnal · Zipf reuse"]
    GA["<b>3 attack scenarios</b><br/>burst · rotating · slow_low"]
    GC["<b>3 negative controls</b><br/>flash_sale · issuer_outage<br/>outage_single_merchant"]

    GB ==> SPLIT
    GA ==> SPLIT
    GC ==> SPLIT

    SPLIT["<b>temporal split</b> — day-50 wall<br/>loader raises on a non-temporal split"]
    SPLIT ==>|"train"| VAL["<b>validation</b> — last 25% of train<br/>every tunable chosen here"]
    SPLIT ==>|"test"| TST["<b>test</b> — read once, after<br/>the operating point is fixed"]

    VAL ==> PY
    TST ==> PY

    PY["<b>reference.py</b> — the specification<br/>5 stages · 4 axes · <b>no Card axis</b><br/>512-slot ring · Welford + EWMA"]
    RS["<b>spandan-core</b> — Rust via PyO3<br/>abi3 · zero-copy numeric columns<br/>same five stages"]
    PY <==>|"bit-exact<br/>0e0 at 1e-9"| RS

    PY ==> DECL["<b>score &gt; threshold → DECLINE</b><br/>inline authorization control<br/>1 in 71 legitimate customers"]

    DECL ==> ALR["<b>alerts</b> — dedup per merchant+BIN<br/>20,254 events → 487 alerts"]
    DECL -. "frozen Flag" .-> LLM["<b>llm/</b> — explanation only<br/>replay-only cassettes"]

    ALR ==> EV["<b>eval/</b> — constrained selection · three precisions · rupee cost model"]
    LLM -. "✗ no import path" .-> EV

    classDef spec fill:#1f4e79,color:#ffffff,stroke:#1f4e79
    classDef rust fill:#8c3d10,color:#ffffff,stroke:#8c3d10
    classDef danger fill:#a4262c,color:#ffffff,stroke:#a4262c
    classDef good fill:#2f6b3a,color:#ffffff,stroke:#2f6b3a
    classDef data fill:#4a4f57,color:#ffffff,stroke:#4a4f57
    classDef bound fill:#3c3f45,color:#ffffff,stroke:#9aa0a6,stroke-width:2px

    class PY spec
    class RS rust
    class DECL danger
    class EV good
    class GB,GA,GC,SPLIT,VAL,TST,ALR data
    class LLM bound
```

Full walk: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Five stages, one per Rust module, mirrored by the Python reference: `ingest` →
`state` → `velocity` → `baseline` → `score`. Per event: advance all four entity
axes, score, then fold baselines — scoring before folding, so an event is never
measured against a baseline containing it.

```mermaid
%%{init: {"flowchart": {"wrappingWidth": 190, "nodeSpacing": 26, "rankSpacing": 34, "curve": "basis"}}}%%
flowchart LR
    S1["<b>1 · ingest</b><br/>typed Event<br/>no label field"]
    S2["<b>2 · state</b><br/>4 axes, no Card<br/>per entity"]
    S3["<b>3 · velocity</b><br/>512-slot ring<br/>5-min half-open"]
    S4["<b>4 · baseline</b><br/>Welford + EWMA<br/>60s sample gate"]
    S5["<b>5 · score</b><br/>4 evidence<br/>− 2 damping"]

    S1 ==> S2 ==> S3 ==> S4 ==> S5
    S5 -. "baselines folded only after scoring" .-> S4

    classDef stage fill:#4a4f57,color:#ffffff,stroke:#4a4f57
    class S1,S2,S3,S4,S5 stage
```

Axes are BIN, IP, device, and merchant. Each holds a 5-minute sliding window over
`(t−W, t]` in a 512-slot ring buffer, plus Welford and EWMA baselines fed on a
60-second sample gate. The score is four evidence terms minus two damping terms, in
fixed summation order.

**`Axis` has no `Card` variant.** No feature may derive from card novelty or
first-seen-ness, because the flash-sale control only partially controls for it —
attack cards here are 100% unseen, flash-sale cards 56.3%, outage cards 0.9%, so a
novelty feature would separate the classes for free. The ban is a compile error in
Rust, a test failure in Python, and a line in
[ASSUMPTIONS.md](python/spandan/gen/ASSUMPTIONS.md) §1.7a.

**One specification, two engines.** `detect/reference.py` is the spec. The window
convention, the baseline fold order, and the six-term summation order were written
down before any Rust existed, which is what bit-exact agreement is made of —
identical operation order over IEEE 754 doubles gives identical results, and parity
risk is spec ambiguity rather than arithmetic. The committed 3,866-event fixture
agrees at tolerance 1e-9 with measured delta 0e0. The escape hatch permitting a
tolerance fallback went unused.

The fixture alone would be weak evidence: its first version had peak ring occupancy
of 58 of 512 and never saturated, so "bit-exact" meant bit-exact on the happy path
until a 900-event mega-burst was added. **The real parity test is the engine swap** —
`make eval ENGINE=rust` against `ENGINE=python` over 1.6M events at full state depth
across three streams and three detector variants. The two metrics JSONs differ in
one line, the engine label. Both statements hold per machine: across C runtimes
the pure-Python reference itself moves at the fourteenth decimal (640 of the 3,866
fixture scores, at most 2.8e-14, [BUILD_LOG](docs/BUILD_LOG.md) 2026-09-03), which
no four-decimal figure sees — the CI `figures` job reproduces all 31 on ubuntu.

**The Rust trade, both halves:** Rust buys a 5.38× streaming throughput gain and a
4.52× better p99 (11.6µs vs 52.4µs) at 2.44× the memory per entity — 4,819 vs 1,971
bytes, projecting 38.6 GB vs 15.8 GB per month at an assumed 8M distinct entities.
Retained events are bounded **per entity**; entities are never freed, so total memory
is **linear in distinct entity count**. Two fixes for the 2× constant are identified
and unbuilt — interning identifiers, right-sizing the ring — because closing a 2×
constant on an unbounded curve is polish, not the fix. The growth curve is the
deployment blocker, and eviction or a sketch is what addresses it, each with an
accuracy cost named in [FAILURE_MODES.md](docs/FAILURE_MODES.md) §7.

## After a flag: the triage graph

A flag is a score above a threshold. What it *becomes* — a decline, an alert, a
hold for a person — is decided by an explicit graph in
[`python/spandan/triage/`](python/spandan/triage/graph.py): nodes are plain
functions over typed state, edges are routing functions declared with every name
they may return, and the graph validates itself on import. The diagram below is
rendered from the edge table (`spandan triage-graph --mermaid`) and a test
asserts the two cannot differ.

```mermaid
%%{init: {"flowchart": {"wrappingWidth": 200, "nodeSpacing": 28, "rankSpacing": 44, "curve": "basis"}}}%%
flowchart LR
    START([flag]) --> dedup
    dedup["dedup<br/>15-min cooldown"]
    exposure["exposure<br/>rupee at risk"]
    kill_switch["kill_switch<br/>retry ratio / hour"]
    mode["<b>mode</b><br/>the decision"]
    act["<b>act</b><br/>decline"]
    human_review["human_review<br/>alert / hold"]
    explain["explain<br/><b>the one LLM node</b>"]
    ground["ground<br/>validator"]
    template["template<br/>fallback"]
    END([end])
    dedup --> exposure
    exposure --> kill_switch
    kill_switch --> mode
    mode --> act
    mode --> human_review
    act --> explain
    human_review --> explain
    human_review --> END
    explain --> ground
    ground --> END
    ground --> template
    template --> END
    classDef plain fill:#4a4f57,color:#fff,stroke:#4a4f57
    classDef llm fill:#3c3f45,color:#fff,stroke:#9aa0a6,stroke-width:2px
    classDef decide fill:#a4262c,color:#fff,stroke:#a4262c
    classDef guard fill:#1f4e79,color:#fff,stroke:#1f4e79
    classDef term fill:#2f6b3a,color:#fff,stroke:#2f6b3a
    class dedup,exposure,human_review,template plain
    class explain llm
    class mode,act decide
    class ground,kill_switch guard
    class START,END term
```

Three properties, each a test rather than a promise:

- **No path from the language model to an action.** `mode` makes the decision
  and audits it; `act` executes it; `explain` runs strictly afterwards and its
  only successor is the validator. `test_no_path_from_explain_to_act` asserts it
  on the topology, and the poisoned-import test runs this whole graph with
  `spandan.llm` unimportable and gets identical decisions.
- **The audit is written before the action.** Every transition appends one JSON
  line — event-stream timestamps, sorted keys — to `data/audit.jsonl`; `act`
  refuses to run unless its decision is already on the trail; the trail is
  byte-identical across runs.
- **It stops itself.** The kill-switch turns inline decline into alert-only for
  one (merchant, BIN) when the trailing hour shows an outage's retry structure —
  attempts per distinct card ≥ 2.5 over ≥ 20 events, the signal §2.2 of
  FAILURE_MODES shows is invisible at five minutes. Chosen on the training
  window, registered in `costs.toml` before the test result was known.

Measured on the test window, scores unchanged: **20 trips, all on the
single-merchant outage control, none on any attack**; every flagged attack still
declines (true positives through the action 9,038 → 9,038); 3,447 of 11,216
false declines become alerts; legitimate transactions declined **1 in 71 → 1 in
102**. A recovery of 31%, late by construction — the switch cannot fire until the
retries are visible — and not a fix. Detail in
[FAILURE_MODES.md](docs/FAILURE_MODES.md) §2.1a.

## What it does not do

Detail and measurements: [docs/FAILURE_MODES.md](docs/FAILURE_MODES.md).

**It cannot distinguish a single-merchant issuer outage from card testing.** A
negative control built to attack the detector's primary signal with its usual
separators removed — one merchant, probe-band amounts — has **50.5% of its events
flagged** (9,170 of 18,169). The highest-scoring clean event scores 80.79 against a
threshold of 21.99: headroom −267%, on every seed. The mechanism is measured. Retries
separate an outage from a probe over a whole episode (4.7 attempts per card vs 1.0),
but the retries are spread across an hour while the window is 5 minutes, so any
single window sees 1.13 in-window attempts per card for an outage against 1.22 for a
burst. The damping term points the wrong way. That failure, not the alert queue, is
what makes the 1-in-71 decline rate irreducible at this window size. The fix — a
second, long-horizon window on the BIN axis — is diagnosed and unbuilt; the detector
was frozen as the parity spec for the Rust port.

Also unaddressed:

- Retry separation and anything slower than the 5-minute window. `slow_low` has the
  lowest event recall of the three attack scenarios at 0.445.
- Cold start. With empty baselines, ordinary traffic scores 17–21 standard deviations
  above a near-zero-variance baseline. Persisting state across restarts is unbuilt.
- Total memory growth, above.
- Adaptive attackers. Scenarios are fixed signatures; no evasion is modelled.
- First-party fraud, mule networks, COD/RTO abuse, and UPI. Different problems with
  different machinery; none share this one's signal.

**The loss this prices, and the loss it does not.** The cost model prices avoided
chargeback exposure, saved authorization fees, and the contribution margin lost on
blocked good transactions. It does not price scheme monitoring-programme costs,
merchant reputation, or customer trust — so the worst failure above costs only
₹11,077 in the model, because outage traffic was mostly declining anyway. That is a
statement about the cost model, not about the severity of the failure.

## Where AI is, and where it is forbidden

One language model, one task: turn a flag into a triage note an analyst can act
on or dismiss in five seconds. It sits outside the code that produces numbers,
and that is proven rather than promised: `spandan.detect` and `spandan.eval`
cannot import it, and the full evaluation runs bit-identically with `spandan.llm`
replaced by an object that raises on any attribute access. `Flag` is frozen; the
model receives a copy and has no write path back.

Measured, it fabricated. Both recorded notes conditioned their next action on
evidence this pipeline does not have — a CVV/AVS result, per-card history, a
cardholder IP — after a prompt stating that the evidence shown was everything
known. The model's output therefore passes through a validator that rejects any
note citing evidence outside the prompt it was generated from: 2 of 2 recorded
notes rejected, the deterministic template accepted by construction, and
`explain_flag` returns a model note only when it is grounded. The cassettes are
committed exactly as returned; re-prompting for a nicer sample would be selecting
on the test set.

The reasoning behind the placement: the decision to decline a payment is a
threshold comparison on six terms with a written summation order, chosen on
validation under a pre-registered budget. Nothing in it needs a model, and the
one model in the system has been shown to invent evidence. Detail in
[FAILURE_MODES.md](docs/FAILURE_MODES.md) §8.

## Method

- **Temporal split.** Train days 0–50, test days 50–100, no event crossing. The
  validation window is the last 25% of train, a suffix and never a sample. The loader
  raises rather than build a non-temporal split. Everything tunable is chosen on
  validation; the test window is read once.
- **The operating point was not moved after seeing test numbers.** The alerts/day
  budget was registered in `costs.toml` before the test window was read (commit
  `e5b48f8`), and its basis — what one analyst can work through in a day — does not
  depend on the results. Tighter budgets score better on test. The frontier printed by
  `make eval` is a sensitivity analysis, not a menu.
- **Alerts bound the queue, not merchant impact.** 20,254 flagged events collapse into
  487 alerts, 42 events per alert, which is why the decline rate is reported wherever
  alerts/day appears.
- **Synthetic data, because no public dataset carries BIN, IP and device together.**
  IEEE-CIS is not card-testing-specific and lacks the axes; PaySim is mobile-money
  with no authorization concept; the Kaggle set is anonymised PCA components. The
  generator is deliberately unkind: benign decline rates run 4.5–11.5%, attack
  episodes borrow BINs that carry real benign traffic, and card novelty is banned.
  Ten ways this stream is unlike real traffic — three of which flatter these results —
  are itemised in [ASSUMPTIONS.md](python/spandan/gen/ASSUMPTIONS.md) §2.
- **Cost model assumptions are labelled in
  [`costs.toml`](python/spandan/eval/costs.toml).** The net position is dominated by
  the chargeback term, which is the product of two uncitable assumptions — a ₹500
  dispute fee and an 0.8 chargeback rate on approved fraud. Halving the rate roughly
  halves the saving. Review cost is reported as a break-even output rather than an
  input for the same reason.
- **Five defects in this project were plausible figures with nothing behind them:**
  single-seed precision of 1.00 on a two-episode test window; a bit-exact parity
  fixture that never filled a ring buffer; 0.0 MB RSS from an unchecked Win32 call; a
  batch=1 benchmark that re-scored one hot event and beat batch=100k; and a patch
  script whose empty verification grep went unchecked. None of these were caught by
  staring harder at the output — each was caught by a guard, a re-run under different
  conditions, or someone asking whether the number should be true. Two published
  findings were withdrawn on the same basis and are kept in place, marked, in
  [FAILURE_MODES.md](docs/FAILURE_MODES.md) §3 and §0.1.
- **The LLM explanation layer cannot reach a number.** `spandan.detect` and
  `spandan.eval` cannot import it, and the full evaluation runs bit-identically with
  `spandan.llm` replaced by an object that raises on attribute access. The recorded
  model output fabricated fields the schema does not contain — CVV/AVS results,
  per-card history — and the cassettes are committed as returned. A validator
  (`llm/grounding.py`) now rejects any note that cites evidence outside the prompt
  it was generated from: 2 of 2 recorded notes rejected, the template passes by
  construction, and `explain_flag` returns a model note only when it is grounded.
  [FAILURE_MODES.md](docs/FAILURE_MODES.md) §8.
- **Recording provenance and data-use disclosure.** Replay is the default and never
  touches the network; recording is opt-in via `SPANDAN_LLM_MODE=record` with the
  key read from the environment — no `.env`, no dotenv loader. Each cassette's
  `recorded_via` names the provider and exact model. The two cassettes that
  constitute the fabrication finding were recorded on the **Gemini API free tier**
  (`gemini-3.1-flash-lite`), where Google may use prompts and responses to improve
  its products. Later cassettes are recorded via the **Groq API**
  (`llama-3.3-70b-versatile`, `GROQ_API_KEY`); Groq's privacy policy and terms of
  use defer API data handling to its Services Agreement and DPA, which were not
  reviewed here. In every case the prompt contains only synthetic identifiers from
  reserved ranges — there is no real card, IP, or merchant to leak.

## Repo map

```
spandan-core/src/     Rust core: ingest, state, velocity, baseline, score, pybridge
python/spandan/gen/   Synthetic stream generator + ASSUMPTIONS.md
python/spandan/detect/  Detector interface, Python reference (the spec), Rust adapter, parity fixture
python/spandan/eval/  Temporal loader, metrics, rupee cost model, evaluation harness, learned baselines, benchmarks
python/spandan/triage/  The post-detection graph: nodes, routing table, audit trail, kill-switch
python/spandan/llm/   Bounded explanation layer, grounding validator, cassettes, comparison target
tests/                125 tests: generator, detector, cross-engine parity, evaluation, triage graph, LLM boundary
docs/                 ARCHITECTURE, FAILURE_MODES, BENCH, BUILD_LOG, PHASES, AUDIT, agents (house rules)
scripts/              check_figures.py - every documented figure against the build (make check)
```

Identifiers are drawn from reserved ranges — ISO/IEC 7812 MII-0 BINs, RFC 5737 and
RFC 2544 addresses, opaque non-PAN card tokens. No card number is generated anywhere.
Attack scenarios are described as statistical signatures only.

`spandan` is unclaimed on PyPI and crates.io, and `spandan-core` on crates.io, as of
2026-08-26. Nothing is published.
