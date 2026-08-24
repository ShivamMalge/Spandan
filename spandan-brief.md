# Spandan — project brief and planning task

## Your task in this session

Read this brief, then write a single file, `PHASES.md`, breaking the work into
phase-gated units I will approve one at a time.

**Write no code this session. No scaffold, no `Cargo.toml`, no stubs, no directories.
The only artifact is `PHASES.md`.** I will review it, edit it, and hand you Phase 0
separately.

If something in this brief is ambiguous, underspecified, or looks like a bad idea,
say so at the end of your response rather than resolving it silently. I would rather
argue about the plan now than discover the disagreement in Phase 4.

---

## The project

**Spandan** is a defense-only streaming fraud-spike detector for card-testing and
velocity abuse in payment streams.

Card testing is the pattern where an attacker validates stolen card numbers by pushing
large volumes of small transactions through a merchant's checkout. The merchant pays
authorization fees on every attempt, absorbs the downstream chargebacks on the ones
that succeed, and often notices only when the statement arrives. It is a distinct loss
class from return/RTO abuse and from dispute handling.

Spandan consumes a transaction event stream and flags anomalous spikes in real time. A
deterministic Rust core maintains per-entity state — per BIN, per IP, per device, per
merchant — computing sliding-window velocity counts, decline ratios, and EWMA plus
Welford baselines, and emits a score per event. A separate, strictly bounded LLM layer
explains a flag to a human analyst after the fact. **The LLM never decides a flag and
never alters a label.** That separation is a hard architectural commitment, not a
preference, and the plan should make it structurally impossible to violate.

### What this is being judged on

This is a submission to a fintech hiring buildathon, reviewed by a payments risk panel
in an architecture walkthrough. The stated bar is a working detector for one class of
loss, with measured precision and recall on a held-out test set, and honest metrics
including false-positive cost. The panel will have seen many submissions consisting of
a Kaggle CSV, a randomly split XGBoost model, and a dashboard reporting 99% accuracy.

What differentiates this submission is therefore not model sophistication. It is:

- **A temporal split.** Training data strictly precedes test data. Random or shuffled
  splits are forbidden anywhere in the project.
- **A false-positive cost model denominated in rupees.** Blocked good transactions plus
  manual review cost per alert, against saved authorization fees and chargeback
  exposure. This is the centrepiece. A detector that flags a legitimate flash sale is
  worse than useless, and the plan must make that measurable rather than asserted.
- **Streaming latency and throughput measured honestly**, including the workloads where
  a NumPy baseline is competitive with the Rust core.
- **A written failure-modes section** describing what the detector misses and what it
  over-flags, backed by measurements.
- **Ablations** showing which features actually carry the signal.

### Data

There is no suitable public dataset. Public fraud datasets are either anonymized to the
point of removing the BIN, IP, and device fields this detector depends on, or they model
a different loss class entirely, and off-the-shelf synthetic tabular generators are
documented to destroy exactly the velocity and multi-entity correlation structure that
matters here.

So the project generates its own labeled synthetic stream: a benign baseline with
realistic diurnal volume, a nonzero benign decline rate, and natural entity reuse, into
which four labeled scenarios are injected — a concentrated card-testing burst; the same
signature with rotating IPs and devices, which defeats naive per-IP velocity rules; a
slow-and-low variant that stays under any fixed threshold; and a benign flash sale that
is labeled clean and must not be flagged.

The honesty of the metrics rests entirely on the honesty of this generator, so the plan
must treat data generation as a first-class phase with its own tests and its own
documentation of assumptions and of the ways the synthetic data is unlike real traffic.

### Stack and scope

Rust core exposed to Python via PyO3, built with maturin, targeting `abi3-py310`. Python
handles data generation, the evaluation harness, and the LLM layer. Demo surface is a
CLI that replays the test stream and prints flags with rupee exposure; a single FastAPI
page is acceptable only if it costs under half a day.

Deliberately out of scope — do not plan for these, and push back if you think one is
necessary:

- SIMD or hand-vectorization. Scalar Rust is sufficient; the win here is bounded-memory
  streaming latency, not raw throughput, and vectorizing scatter-heavy per-entity
  updates is a known dead end.
- Custom hash maps or allocators. Use the standard library until profiling says
  otherwise.
- Graph-based ring detection, union-find over shared entities, or any clustering layer.
- A database, Docker, a web frontend beyond the one page, or a trained ML model. The
  detector is statistical and deterministic by design.

### Constraints

- **Solo build, roughly twelve days, ending September 3.** Plan for a real person with
  other obligations, not a continuous sprint. Leave slack.
- **Every LLM call goes through a provider abstraction with cassette record and replay.**
  The test suite runs fully offline with no API key present.
- **Defense-only.** The system detects; it never generates attacks. Scenario code and
  documentation describe statistical signatures — rate, entity concentration, amount
  band, decline ratio — and never read as a procedure. All identifiers are synthetic:
  no real BINs, no real card numbers, no real IPs, no live endpoints.
- **Evidence over claims.** Every phase must end in artifacts I can inspect: pasted
  command output, a metrics table, a diff. Not a status report.

---

## What `PHASES.md` must contain

For each phase:

1. **Goal** — one sentence.
2. **In scope** — the specific modules, files, and behaviours.
3. **Explicitly out of scope** — what belongs to a later phase and must not be started.
4. **Acceptance criteria** — concrete, checkable, expressed as commands whose output I
   will read. "Tests pass" is not an acceptance criterion; `pytest tests/test_gen.py -v`
   with all cases named and passing is.
5. **Estimated effort in days**, and the risk that the estimate is wrong.

Then, at the end of the file:

- A **day-by-day schedule** from August 24 to September 3, with slack marked as slack.
- A **risk register**: the five or six things most likely to sink this, each with a
  mitigation and a trigger for when to abandon versus push through.
- A **cut list**, ordered: if I am three days behind on August 31, what comes out first,
  second, third, and what is load-bearing enough that the submission is not worth making
  without it.

Order the phases so that the riskiest unknown is resolved early rather than late, and so
that there is a demonstrable, submittable artifact well before the deadline even if
later phases are cut.

Aim for five to seven phases. Fewer means the gates are too coarse to catch drift; more
means I spend the twelve days reviewing instead of building.
