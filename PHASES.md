# PHASES.md — Spandan build plan

Seven phases (0–6), stop-and-wait gates per `agents.md` §4. Every acceptance
criterion below is a command whose output goes in the review, not a claim.

**Approved Aug 24 with six edits; edits applied, provenance recorded at the foot of
this file.**

Total estimated work: **9.75 days** across **11 calendar days** (Aug 24 – Sep 3).
That leaves ~1.25 days of true slack. It is thin. The cut list at the end of this
file, not the schedule, is the real slack — read it as part of the plan, not as an
appendix.

## Ordering rationale (read before approving)

The riskiest unknown in this project is **not** whether a Rust EWMA/Welford core
can be written. It is whether the four scenarios separate from the benign baseline
at a threshold whose false-positive cost in rupees is defensible — in particular
whether *slow-and-low* is detectable at all, and whether the *flash sale* stays
unflagged. That is a joint property of the generator and the scoring, and it is
answerable in Python in two days.

`RESEARCH.md` puts the Rust core at Phase 2 and the eval harness at Phase 4. Under
that ordering, four days of Rust are spent before anyone knows whether the signal
exists, and the answer arrives on Aug 31 with three days left. This plan inverts it:

- **Phase 2 builds a Python/NumPy reference detector plus the full eval harness.**
  It retires the project's central risk on Aug 27, and it produces a complete,
  submittable artifact — generator, detector, metrics, cost model, replay CLI —
  seven days before the deadline.
- **The reference detector is not throwaway.** It is (a) the written spec the Rust
  core is tested against for numerical parity, and (b) the NumPy baseline the
  Phase 4 benchmark table requires. Phase 3 would have needed both built anyway.
- **Phase 0 includes a PyO3/maturin smoke test** (one throwaway `_smoke_add`
  function), so toolchain risk on Windows is retired in half a day on day 1
  instead of surfacing on day 5.

Consequence: if the schedule collapses, the thing that gets cut is the Rust core,
and the submission still stands on the evaluation, which is the centrepiece anyway.
If you would rather the Rust core be uncuttable, say so and I will re-order — but
then the signal-separation question moves to Aug 31 and there is no fallback.

---

## Phase 0 — Repo skeleton, house rules, toolchain smoke test

**Goal.** The repo exists, builds, and the PyO3/maturin/abi3 toolchain is proven on
this machine before a single line of real Rust is written.

**In scope.**
- Directory layout: `spandan-core/` (Rust crate), `python/spandan/` (empty
  `gen/`, `detect/`, `eval/`, `llm/` packages), `tests/`, `data/` (gitignored),
  `docs/`.
- `spandan-core/Cargo.toml` — `pyo3` with `abi3-py310`, pinned minor version.
  `pyproject.toml` — maturin backend, pinned. Versions recorded in the phase
  evidence.
- `Makefile` with all targets declared: `setup`, `data`, `eval`, `bench`, `test`,
  `demo`, `all`. Targets for phases not yet built print
  `not implemented until phase N` and exit non-zero.
- `agents.md` committed. `BUILD_LOG.md` created with a header and no entries.
  `README.md` placeholder — one line, no claims.
- `.gitignore` — ignores generated stream data only, scoped as `/data/` (leading
  slash, root-anchored). It must **not** match `tests/fixtures/`, which holds the
  committed Phase 3 parity fixture. Phase 0 asserts this with
  `git check-ignore -v tests/fixtures/.keep` returning non-zero.
- One throwaway PyO3 function `_smoke_add(a, b)` and `__version__`, whose only
  purpose is proving the wheel builds and imports. **Deleted in Phase 3.**
- One trivial pytest so `make test` has something to collect.

**Explicitly out of scope.** Any detector logic. Any generator logic. Any of the
five Rust modules from the architecture. README content beyond a placeholder. CI,
Docker, dependency additions beyond pyo3/maturin/pytest/numpy.

**Acceptance criteria.**

```
cargo --version && maturin --version && python -VV && pip show maturin | head -2
maturin develop --release                 # output through "Installed spandan-core"
python -c "import spandan_core as s; print(s.__version__, s._smoke_add(2,3))"
                                          # expect: 0.1.0 5
make test                                 # pytest -q, 1 passed
make eval                                 # expect: not implemented until phase 2, exit 2
git check-ignore -v tests/fixtures/.keep ; echo "exit=$?"   # expect exit=1 (not ignored)
git log --oneline && git show --stat HEAD  # agents.md present in history
```

**Effort.** 0.5 day. **Risk the estimate is wrong: low**, with one fat tail — an
MSVC linker or abi3 mismatch on Windows. That tail is precisely why this is day 1
and not day 5. If `maturin develop --release` is not green in two hours, stop and
report; do not start Phase 1 on top of a broken toolchain.

---

## Phase 1 — Synthetic stream generator and temporal split

**Goal.** A deterministic, documented, strictly time-ordered labeled event stream,
split into an earlier train window and a strictly later test window.

**In scope.**
- `python/spandan/gen/schema.py` — frozen event record: `ts`, `txn_id`,
  `merchant_id`, `bin`, `card_ref` (synthetic opaque id), `ip`, `device_id`,
  `amount_paise`, `status` ∈ {approved, declined}, `label`, `scenario_id`.
  Feature columns are a frozen explicit list that excludes `label` and
  `scenario_id`.
- `python/spandan/gen/baseline.py` — benign traffic: per-merchant diurnal volume
  curve, amount distribution, a **nonzero** benign decline rate, and natural entity
  reuse (repeat cards/devices/IPs with a documented repeat distribution).
- `python/spandan/gen/scenarios.py` — four labeled fixtures, described only as
  statistical signatures (rate, entity concentration, amount band, decline ratio),
  per `agents.md` §7: concentrated burst; rotating-IP/device variant;
  slow-and-low sub-threshold variant; benign flash sale labeled **clean**.
- `python/spandan/gen/ASSUMPTIONS.md` — every distributional choice, its source or
  its lack of one, and an explicit list of ways this stream is unlike real traffic.
  This file is graded content, not a comment.
- Explicit seed threaded through every draw (`agents.md` §5). Writer to
  gzipped JSONL. `data/manifest.json` recording seed, config hash, counts, ranges.
- `make data`, and `python -m spandan.gen.summary`.

**Explicitly out of scope.** Any scoring, threshold, or detector. Any code that
reads `label` for anything except computing the summary. The Rust core. Plots.
Tuning distributions to be pretty — realism is capped at ASSUMPTIONS.md plus the
tests below until Phase 2 reports whether the signal separates.

**Acceptance criteria.**

```
make data
ls -la data/ && cat data/manifest.json
python -m spandan.gen.summary
  # prints per split: rows, min/max ts, overall positive rate, positive rate per
  # scenario, entity cardinalities (bin/ip/device/merchant), benign decline rate,
  # and the literal line: max(train.ts) < min(test.ts): True
pytest tests/test_gen.py -v
  # all named and passing:
  #   test_seed_reproducible_byte_identical
  #   test_events_monotonic_nondecreasing_ts
  #   test_train_strictly_precedes_test
  #   test_feature_columns_exclude_label_and_scenario
  #   test_benign_decline_rate_within_declared_band
  #   test_flash_sale_events_labeled_clean
  #   test_scenario_positive_counts_match_manifest
  #   test_all_identifiers_synthetic   # BINs from reserved test range,
  #                                    # IPs from TEST-NET blocks (RFC 5737)
make data && sha256sum data/*.jsonl.gz   # identical to the first run's hashes
```

**Effort.** 1.5 days. **Risk the estimate is wrong: medium-high.** Not because the
code is hard, but because "make the benign baseline convincing" has no natural
stopping point and can absorb three days. The timebox is the eight tests plus
ASSUMPTIONS.md. If Phase 1 is not gated by end of Aug 25, ship it as-is and let
Phase 2's numbers tell you which distribution actually needed the work.

---

## Phase 2 — Python reference detector, eval harness, rupee cost model

**This is the risk gate. If one phase gets the slack, it is this one.**

**Goal.** Measure whether the four scenarios separate from the baseline at a
threshold whose false-positive cost in rupees is defensible — with a Python
reference implementation that doubles as the Rust core's spec and the benchmark's
NumPy baseline.

**In scope.**
- `python/spandan/detect/interface.py` — `Detector` protocol:
  `update(event) -> Flag | None` and `score_batch(arr) -> np.ndarray`. `Flag` is a
  **frozen** dataclass. This interface is the seam the Rust core swaps into in
  Phase 4 and the boundary the LLM cannot cross in Phase 5.
- `python/spandan/detect/reference.py` — the five-stage pipeline the Rust core will
  mirror: per-entity state (BIN, IP, device, merchant), sliding-window counts and
  decline ratio, EWMA plus sliding-window Welford baseline, deviation → spike
  score. Window-boundary convention (inclusive/exclusive edges, tie handling)
  written explicitly in the module docstring — Phase 3's parity work depends on it.
- `python/spandan/eval/` — loader that **refuses** a non-temporal split; a
  validation window carved from the training period (all thresholding happens
  there, `agents.md` §6); precision, recall, F1, PR-AUC; per-scenario recall
  breakdown; ablation runner (drop EWMA / drop Welford / drop per-IP / drop
  per-device — all four; trimming is a Sep 1 decision, not a Phase 2 one).
- **Cost-vs-threshold sweep** (replaces the calibration curve, which is dropped on
  merit: the score is a deviation from a per-entity baseline, not a probability
  estimate, so calibration answers a question nobody asked). Net rupee position
  across the full threshold range, with the chosen operating point marked and the
  sweep minimum marked. This is the argument for *why that threshold*, not a
  decoration.
- **Time-to-detection, per scenario** — median events elapsed and median rupees
  exposed before the first flag on that scenario, plus the same at p90. For a spike
  detector this is closer to the product than recall is: catching the burst at
  attempt 40 and catching it at attempt 400 are both "recall = 1" and are not the
  same outcome. On the load-bearing floor.
- **Multi-seed stability** — the full eval re-run on three generator seeds, with the
  spread (min/median/max) on every headline metric. If the spread is wide the
  numbers are noise, and that has to surface here rather than in the write-up.
- `python/spandan/eval/costs.toml` — cost parameters with their anchors: blocked-
  good-transaction value, saved authorization fee, avoided chargeback exposure.
  **The manual-review cost per alert is reported as an output, not taken as an
  input**: the harness prints the *break-even* review cost — "net position stays
  positive as long as an analyst review costs under ₹X per alert" — so the one
  parameter with no defensible source stops being an assumption the argument rests
  on. `costs.toml` still carries a stated assumption for the headline figure, but
  the break-even line leads. Paired with **alerts per day at the chosen threshold**,
  so the operational question — can one human actually process this volume — is
  answered rather than implied. Net position also reported separately for the
  flash-sale window.
- `spandan replay <stream>` CLI: streams events, prints flags with running rupee
  exposure.
- `make eval`. Draft `docs/FAILURE_MODES.md` — what it misses, what it over-flags,
  with the numbers that show it.

**Explicitly out of scope.** Rust. PyO3. The LLM layer. A calibration curve (dropped
on merit — see above). Any plot beyond the PR curve and the cost-vs-threshold sweep.
Any change to the generator made *after* seeing test-set numbers — if you believe the
generator is wrong, say so, log it in `BUILD_LOG.md`, and re-generate under a new
seed with the reasoning recorded.

**Acceptance criteria.**

```
make eval
  # prints, in order:
  #   threshold provenance line: chosen on validation window [t0, t1), never test
  #   metrics table: precision, recall, F1, PR-AUC at the chosen threshold
  #   per-scenario recall: burst / rotating / slow-and-low / (flash sale: FP count)
  #   time-to-detection per scenario: median and p90 events elapsed, rupees exposed
  #   flash-sale false positives and their rupee cost
  #   cost table: saved auth fees + avoided chargeback exposure
  #               - blocked-good value = GROSS rupees
  #   BREAK-EVEN review cost: net positive while review < Rs.X per alert
  #   alerts per day at the chosen threshold
  #   cost-vs-threshold sweep: net rupees across the range, chosen point marked
  #   ablation table: full / -EWMA / -Welford / -perIP / -perDevice
  #   multi-seed spread: min/median/max per headline metric over 3 seeds
make eval SEEDS=3    # or however the harness takes it; the spread table is the output
pytest tests/test_eval.py -v
  #   test_loader_rejects_nontemporal_split
  #   test_threshold_selection_never_touches_test_window
  #   test_cost_model_matches_hand_worked_example
  #   test_break_even_review_cost_matches_hand_worked_example
  #   test_break_even_is_computed_not_read_from_costs_toml
  #   test_pr_auc_matches_sklearn_reference
  #   test_ablation_toggle_changes_active_feature_set
  #   test_flash_sale_alert_count_is_reported_not_suppressed
  #   test_time_to_detection_counts_events_before_first_flag
  #   test_time_to_detection_is_infinite_when_scenario_never_flagged
  #   test_multi_seed_spread_reported_for_all_headline_metrics
pytest tests/test_reference_detector.py -v
  #   test_welford_matches_two_pass
  #   test_window_counts_match_bruteforce
  #   test_decline_ratio_matches_bruteforce
  #   test_deterministic_across_runs
spandan replay data/test.jsonl.gz --limit 5000
  # pasted output showing the flash-sale window passing clean and a burst flagged
make eval > /tmp/a.txt && make eval > /tmp/b.txt && diff /tmp/a.txt /tmp/b.txt
```

**Effort.** 2.25 days, and it is the phase that should get the first half-day of
slack. **Risk the estimate is wrong: high** — but the risk is in the *answer*, not
the code. "Slow-and-low is not detectable below an unacceptable FP cost" is a
legitimate Phase 2 outcome and a strong failure-modes section. The gate is that the
number is measured and reported, not that it is good. One round of window-size/
threshold iteration is budgeted, on the validation window only.

**Accounting, settled Aug 24:** dropping the calibration curve frees nothing. The
cost-vs-threshold sweep replaces it at roughly equal cost and additions 5 and 6
(multi-seed, time-to-detection) are net new work — a quarter-day increase, which is
why Phase 2 is 2.25 days and the project total is 9.75 across 11. Phase 3's parity
risk is therefore covered by the pre-committed one-day hard stop and by nothing
else.

> **Escalation, not silent absorption.** If Phase 2 has not gated by end of
> **Aug 27**, flag it that day and cut ablations to two immediately (cut-list item
> 2, pulled forward from the Aug 31 decision point). Do not absorb a Phase 2
> overrun into Sep 1 — Sep 1 is a single slack day and a Phase 2 overrun plus a
> Phase 3 overrun would both land on it.

---

## Phase 3 — Rust core, clean-room, parity-tested

**Goal.** The five modules in scalar Rust, with property tests and numerical parity
to the Phase 2 reference.

**In scope.**
- `spandan-core/src/` — exactly five modules: `ingest`, `state`, `velocity`,
  `baseline`, `score`. No sixth.
- Fixed-capacity ring buffers per entity (bounded memory).
  `std::collections::HashMap` keyed by entity id.
- Unit tests per module. Property tests — **requesting `proptest` as a dev
  dependency, per `agents.md` §8; it is the only new crate this phase asks for.**
- A parity fixture: `tests/fixtures/parity.json`, exported by Phase 2 (event vector
  plus expected scores), replayed in a Rust test. **Write this test first**, not
  last. The fixture is **committed to git**, not generated at test time — the Rust
  test must be runnable in a fresh clone with no Python step. `/data/` is
  root-anchored in `.gitignore` precisely so it cannot swallow this path.
- Delete the Phase 0 `_smoke_add`.

**Explicitly out of scope.** The PyO3 surface (Phase 4) beyond what Phase 0 already
built. Benchmarks (Phase 4). SIMD, custom hash maps, custom allocators, `rayon`,
threads — cut by decision, not schedule. Any entity axis beyond BIN/IP/device/
merchant. Any graph, union-find, or clustering layer.

**Acceptance criteria.**

```
cargo test --all -- --nocapture
  # full output, with these visible and passing:
  #   velocity::tests::window_count_matches_bruteforce
  #   velocity::tests::ring_buffer_never_exceeds_capacity   (proptest)
  #   baseline::tests::welford_matches_two_pass_within_tol   (proptest)
  #   baseline::tests::ewma_bounded_by_input_extremes        (proptest)
  #   state::tests::memory_bounded_under_entity_churn
  #   score::tests::parity_with_python_reference_fixture
  #     -> prints max abs score delta and the tolerance it was checked against
cargo test --release            # green too; release float paths differ
cargo clippy --all-targets -- -D warnings
grep -rn "_smoke_add" spandan-core/ python/ ; echo "grep exit=$?"   # expect exit=1
git ls-files --error-unmatch tests/fixtures/parity.json   # fixture is committed
```

**Effort.** 2.5 days, **stated as optimistic and deliberately not padded.**
Independent scoping put the clean-room core at ~4 days before the parity fixture is
counted, and the fixture is real additional work on top. The estimate stays at 2.5
because padding it would just relocate the overrun; the overrun is handled by the
hard stop below instead.

**Risk the estimate is wrong: high, and pre-committed.** The estimator maths is
small; the day-eater is parity — window-boundary conventions and float ordering
between two implementations.

> **Pre-committed escape hatch (no judgement call at the time).** The
> window-boundary convention is written down in Phase 2 and the parity test is the
> first Rust test authored. If parity does not pass at a stated tolerance **by end
> of day one of Phase 3**, it ships as-is: the tolerance is documented in the test
> output, a `BUILD_LOG.md` entry names the discrepancy and what was tried, and the
> phase moves on. **No exactness chase. No second day on parity.** This is decided
> now, not on Aug 28.
>
> Confirmed Aug 24: with the accounting settled at 9.75 days across 11, this stop
> is **load-bearing rather than a safety net**. Hold it strictly — one day, then
> ship with the documented tolerance regardless of how close it feels.

If the core itself (not parity) is still incomplete at end of day three, the cut
list governs — see item 4.

---

## Phase 4 — PyO3 bindings, engine swap, honest benchmarks

**Goal.** The Rust core runs behind the Phase 2 `Detector` interface, the identical
eval reproduces through it, and the Rust-vs-NumPy table is published as measured.

**In scope.**
- `score_batch` via zero-copy `PyReadonlyArray` (`numpy` crate); streaming
  `Detector.update`.
- `--engine rust|python` on `spandan replay`; `ENGINE=` on `make eval`.
- `python/spandan_core/__init__.py` re-exports the new surface **by name**
  (`from ._native import Detector, score_batch, __version__`) with `__all__`
  updated to match. Never a star-import: `help(spandan_core.Detector)` would pass
  under a star-import while `__all__`-based checks and editor completion quietly
  would not.
- `make bench`: events/sec, p50/p95/p99 per-event latency, peak RSS, across batch
  sizes chosen to span the regime where NumPy is competitive **and** the regime
  where it is not.
- `docs/BENCH.md`: which regime favours which, in prose, including the losses.

**Explicitly out of scope.** Optimizing anything the benchmark exposes as slow —
record it, do not chase it. SIMD, threading. Changing the algorithm, the batch
sizes, or the workload to make Rust look better.

**Acceptance criteria.**

```
maturin develop --release
make bench
  # raw table pasted, including at least one row where NumPy wins or ties,
  # and a sentence saying so
make eval ENGINE=python > /tmp/py.json ; make eval ENGINE=rust > /tmp/rs.json
diff /tmp/py.json /tmp/rs.json    # or a pasted per-metric delta within stated tol
pytest tests/test_parity.py -v
  #   test_rust_python_scores_within_tolerance
  #   test_streaming_update_matches_score_batch
  #   test_zero_copy_does_not_mutate_input_array
python -c "import spandan_core; help(spandan_core.Detector)"
```

**Effort.** 1 day. **Risk the estimate is wrong: medium** — abi3 plus `numpy`-crate
version alignment is the known snag. Escape hatch: if zero-copy fights back for
more than half a day, fall back to a copying `Vec<f64>` conversion and report the
copy cost as a row in the bench table. That is an honest result, not a failure.

---

## Phase 5 — Bounded LLM explanation layer

**Goal.** A flag becomes an analyst-facing explanation, fully offline, with the
"LLM never decides" boundary enforced by tests rather than by discipline.

**In scope.**
- `python/spandan/llm/provider.py` — the single egress point (`agents.md` §5).
  Cassette record/replay keyed by a hash of the rendered prompt plus model id.
  `SPANDAN_LLM_MODE=replay|record`, default `replay`.
- One bounded task: `explain_flag(flag: Flag) -> str`. The prompt is assembled only
  from the frozen `Flag` fields (BIN, window, velocity vs baseline, decline ratio,
  rupee exposure). Return type is a string. There is no code path by which it
  reaches a score, a threshold, or a label.
- Cassettes committed. `spandan explain --flag-id <id>`.

**Explicitly out of scope.** Tool use, agent loops, a second provider, more than one
task, token streaming, letting the LLM see raw events, any LLM involvement in
`make eval`'s numbers.

**Acceptance criteria.**

```
env -u ANTHROPIC_API_KEY pytest tests/test_llm.py -v     # conftest blocks sockets
  #   test_replay_from_cassette_with_no_network_and_no_key
  #   test_missing_cassette_raises_loudly_not_silently
  #   test_flag_dataclass_is_frozen
  #   test_explain_does_not_mutate_flag
  #   test_detect_and_eval_import_graphs_exclude_spandan_llm
  #     -> walks imports of spandan.detect and spandan.eval, asserts spandan.llm absent
  #   test_eval_runs_with_llm_import_poisoned
  #     -> sys.modules['spandan.llm'] raises on access; make eval still green
env -u ANTHROPIC_API_KEY make eval        # unchanged numbers
spandan explain --flag-id <id>            # sample explanation pasted
```

**Effort.** 0.75 day. **Risk the estimate is wrong: low.**

---

## Phase 6 — Submission package

**Goal.** A fresh clone reproduces every number in the README, and a reviewer can
walk the architecture in five minutes.

**In scope.** `README.md` — the loss class and why it is unaddressed, architecture,
how to run, headline metrics **including the FP cost in rupees**, and limitations.
Architecture diagram checked in as SVG or ASCII in `docs/`.
`docs/FAILURE_MODES.md` finalized against final numbers. Final ablation table.
`BUILD_LOG.md` review pass. `make all` from a clean clone. Pitch-video checklist.
Name availability on PyPI and crates.io checked and the result recorded.

**Explicitly out of scope.** The Razorpay `payment.dispute.created` webhook stub —
stretch only, and only if Phase 6 gates before Sep 3 midday. Any new detector
feature. Any refactor.

**Acceptance criteria.**

```
git clone . /tmp/spandan-fresh && cd /tmp/spandan-fresh
python -m venv .venv && . .venv/Scripts/activate    # fresh env INSIDE the clone
python -c "import sys; print(sys.executable)"       # must be the clone's .venv
make setup && make all                              # full output; numbers match README exactly
pytest -q && cargo test
git status --porcelain ; echo "porcelain exit=$? (output must be EMPTY)"
ls docs/            # diagram + FAILURE_MODES.md + BENCH.md
grep -c '^## ' BUILD_LOG.md                 # >= 4 entries
```

Two criteria here are doing specific work and must not be softened:

- **The venv is created inside the clone**, not inherited. The failure this catches
  is not the stray `C:\Users\shiva\.venv` on the build machine — it is `make all`
  passing because the global site-packages happens to hold something the clone
  never declares in `pyproject.toml`.
- **`git status --porcelain` must print nothing after `make all`.** This catches the
  whole class of build-artifact leakage — the Phase 0 `.pdb` was one instance of it
  — rather than one file at a time.

**Effort.** 1.25 days. **Risk the estimate is wrong: low-medium.** README and the
failure-modes write-up are the two places where "one more pass" is always
available; the fresh-clone reproduction is the hard stop.

---

## Day-by-day schedule, Aug 24 – Sep 3

| Date | Day | Plan |
|---|---|---|
| Aug 24 | Mon | Phase 0 (0.5d) → gate. Start Phase 1. *Partial day — this planning session is on it.* |
| Aug 25 | Tue | Finish Phase 1 → gate. |
| Aug 26 | Wed | Phase 2, first half. |
| Aug 27 | Thu | Finish Phase 2 → gate. **First submittable artifact exists from here on.** |
| Aug 28 | Fri | Phase 3, day 1 — parity fixture test first. |
| Aug 29 | Sat | Phase 3, day 2. |
| Aug 30 | Sun | Phase 3, day 3 → gate. **SLACK if Phase 3 gated on Aug 29.** |
| Aug 31 | Mon | Phase 4 → gate. **Decision point: consult the cut list against actual state.** |
| Sep 1 | Tue | **SLACK — no phase assigned.** Absorbs Phase 2 or Phase 3 overrun. |
| Sep 2 | Wed | Phase 5 (0.75d) → gate. Start Phase 6. |
| Sep 3 | Thu | Finish Phase 6 → gate. Record video. Submit before end of day. |
| Sep 4–5 | Fri–Sat | Hard buffer only. Not planned work. Do not spend in advance. |

Phase 3's three days land Fri/Sat/Sun deliberately — it is the longest continuous
stretch and the weekend is where a solo builder with other obligations most likely
has continuous hours. If that assumption is wrong for you, say so now; it changes
the schedule more than any other single input.

The schedule assumes roughly six focused hours a day. It is not a sprint plan, but
1.25 days of slack over 11 days is not generous either. The cut list is the rest of
the slack.

---

## Risk register

**1. Slow-and-low is not detectable at an acceptable false-positive cost.**
*Mitigation:* Phase 2 measures it on Aug 27, not Aug 31, and reports it per
scenario. A negative result becomes the strongest paragraph in FAILURE_MODES.md,
which the panel's bar explicitly asks for.
*Trigger:* if by end of Aug 27 slow-and-low recall is near zero at the chosen
threshold — **push through and report it**. Do not lower the threshold to buy
recall; check the flash-sale FP cost first and let the cost model decide. Abandon
only the *claim*, never the measurement.

**2. The synthetic generator reads as unconvincing to a payments risk panel.**
*Mitigation:* ASSUMPTIONS.md documents every choice and every divergence from real
traffic, so the honesty is auditable even where the realism is imperfect. The
benign flash-sale negative case is the proof that over-flagging was tested.
*Trigger:* **this risk has no fallback, and that is recorded deliberately.** The
IEEE-CIS anchor proposed in `RESEARCH.md` is struck: it is competition-gated, it
models a different loss class, and anchoring against it invites a panel question
with no good answer. ASSUMPTIONS.md is the entire mitigation. If the baseline still
looks implausible by Aug 30, the response is more documentation of the divergence,
not a substitute distribution — and certainly never anyone else's labels.

**3. Rust↔Python parity consumes Phase 3.**
*Mitigation:* window-boundary convention written down in Phase 2; parity fixture is
the first Rust test authored; tolerance stated rather than assumed.
*Trigger:* **hard stop at end of day one of Phase 3** — ship with the documented
tolerance and a BUILD_LOG entry naming the discrepancy. There is no second day on
parity and no judgement call to make on the day; it is pre-committed in Phase 3
above. If the *core* is incomplete at end of day three, cut-list item 4 governs.

**4. PyO3 / abi3 / numpy-crate toolchain fight on Windows.**
*Mitigation:* smoke-tested in Phase 0 on day 1, before anything depends on it.
*Trigger:* Phase 0 not green in two hours → stop and report before starting
Phase 1. In Phase 4, zero-copy not working in half a day → fall back to a copying
conversion and report the copy cost in the bench table.

**5. Real-life schedule slip — the plan assumes ~6 focused hours/day.**
*Mitigation:* the Aug 27 artifact means any single lost day after that degrades the
submission rather than voiding it. Sep 1 is unassigned.
*Trigger:* two full days lost by Aug 31 → execute cut-list items 1–4 immediately
and do not re-evaluate daily. Three days lost → items 1–5, and treat Rust as a
stretch.

**6. Scope creep into the cut items.**
*Mitigation:* ring detection, SIMD, custom hash maps and allocators, and any
clustering layer are cut by decision in `agents.md` §8 and named as out-of-scope in
every phase above. The FastAPI page is not "acceptable if cheap" here — it is
cut-list item 1.
*Trigger:* if any of these is started, the phase is not gated. There is no
push-through case.

**7. Accidental test-set leakage.**
*Mitigation:* thresholds and window sizes are chosen only on a validation window
carved from the training period; the loader refuses a non-temporal split; a named
test asserts the threshold path never reads test timestamps.
*Trigger:* if any metric looks surprisingly good, treat it as a suspected leak and
investigate before reporting it (`agents.md` §6) — and put the investigation in
BUILD_LOG whichever way it resolves.

---

## Cut list, in order

If Aug 31 arrives three days behind, cut in this order and stop cutting as soon as
the schedule closes.

The one-page FastAPI view and the calibration curve are **no longer on this list** —
both are cut outright by decision (the CLI demos better on video; calibration
answers no question the panel asked). They are not schedule levers.

1. **The Razorpay webhook / evidence-mapper stretch.** Formally dead on Aug 31.
2. **Ablations trimmed from four to two** — keep drop-EWMA and drop-per-IP, the two
   the panel will ask about. ~3 hours. **This is a Sep 1 decision; Phase 2 builds
   all four.**
3. **The per-device entity axis.** Costs recall on the rotating-IP/device scenario.
   Report the loss as a measured failure mode; that is on-message, not an excuse.
4. **The Rust core** — ship the Python engine only, and say so in the README
   without spin. This is the largest credibility loss on the list: the architecture
   walkthrough is partly a Rust-streaming story. Only if Phase 3 has not gated by
   Sep 1, and only after items 1–3.

**Not cuttable, though both are cheap enough to look like candidates:**
time-to-detection (it is on the load-bearing floor), and multi-seed stability —
reporting a single seed's numbers as if they were stable is precisely the failure
this project exists to avoid. Reduce to two seeds before dropping it.

**Cut before the LLM layer, not after.** At 0.75 day the LLM layer is cheaper than
almost everything above it, and this is an AI buildathon track — a submission with
no LLM component reads worse than one with a Python engine. If it must go, replace
it with a documented "not built, here is the design and why it is bounded" section
rather than shipping something half-wired.

### Load-bearing floor — not worth submitting without

- The generator with a strict temporal split and ASSUMPTIONS.md.
- Precision, recall, and PR-AUC per scenario, including the flash-sale
  false-positive count.
- **Time-to-detection per scenario** — median events and rupees exposed before the
  first flag.
- The rupee cost model with cited parameters, a net position, the **break-even
  review cost**, and alerts per day at the chosen threshold.
- `spandan replay` — a live demo of a flag with rupee exposure.
- `docs/FAILURE_MODES.md` backed by measurements.
- `README.md` and `BUILD_LOG.md`.

If any of these six is missing on Sep 3, the submission is a dashboard with a story,
which is the thing this plan exists to avoid.

---

## Deviations from RESEARCH.md, for your review

1. **Eval before Rust.** Phase 2 is the Python reference detector plus the full eval
   harness; the Rust core moves to Phase 3. Retires the central risk on Aug 27 and
   yields a submittable artifact seven days early. The reference is also the Rust
   parity spec and the required NumPy benchmark baseline, so it is not extra work.
2. **Toolchain smoke test in Phase 0.** A throwaway `_smoke_add` through maturin,
   deleted in Phase 3. Windows toolchain risk dies on day 1.
3. **Seven phases, not seven-plus-stretch.** `RESEARCH.md`'s Phase 3 (bindings +
   bench) survives as Phase 4; its Phase 6 polish survives as Phase 6.
4. **The FastAPI page is cut outright**, not "acceptable if under half a day."
   Half a day is 5% of the budget and the CLI demos better on video.
5. **The LLM boundary is test-enforced, not policy-enforced** — an import-graph test
   plus a poisoned-import test, so the separation is structural as the brief asks.
   Approved as sufficient; no separate process.
6. **`proptest` is the only new dependency requested**, in Phase 3, per
   `agents.md` §8.
7. **No calibration curve.** Replaced by a cost-vs-threshold sweep — the score is a
   baseline deviation, not a probability.
8. **The manual-review cost is an output, not an input** — reported as a break-even
   figure, so the argument does not rest on an uncitable number.

---

## Review edits applied — Aug 24

Approved with six edits, all applied above:

1. **Phase 3 estimate left at 2.5 days, unpadded**, with the parity escape hatch
   pre-committed to a hard stop at end of day one. Independent scoping of ~4 days
   for the core is recorded in the phase.
2. **Calibration curve dropped on merit**, replaced by a cost-vs-threshold sweep
   with the operating point and the sweep minimum marked.
3. **Break-even review cost** replaces an uncitable review-cost input, paired with
   alerts per day at the chosen threshold.
4. **IEEE-CIS anchor struck** from risk 2; the risk is recorded as having no
   fallback.
5. **Multi-seed stability** added to Phase 2 — three seeds, spread on every headline
   metric.
6. **Time-to-detection per scenario** added to Phase 2 and to the load-bearing
   floor.

Mechanical: `/data/` is root-anchored in `.gitignore` and Phase 0 asserts
`tests/fixtures/` is not ignored, so the committed parity fixture cannot be
swallowed.

**Open, not resolved (asked twice, blank twice):** the availability question in the
schedule section — are Fri/Sat/Sun continuous hours, or heavier with other
obligations? Both approval notes returned it as an unfilled placeholder. Phase 3
stays on Fri/Sat/Sun on the original assumption until answered. It is the single
input that moves the schedule most, and with slack down to 1.25 days it is worth
one line to close.

## Second review pass — Aug 24, after Phase 0

1. **Extension layout approved** as the standard maturin mixed-project arrangement.
   Re-export must stay explicit by name; pinned into Phase 4's in-scope list.
2. **Build-artifact leakage** generalized into Phase 6: `git status --porcelain`
   must be empty after `make all`.
3. **Availability** still open; see above.
4. **Accounting** settled at 9.75 days across 11, making Phase 3's parity stop
   load-bearing. Phase 2 escalates on Aug 27 rather than absorbing into Sep 1.
5. **Stray venv** is not to be deleted or modified. Phase 6 instead builds a fresh
   venv inside the clone, which tests the real risk: undeclared dependencies
   satisfied by the global environment.
