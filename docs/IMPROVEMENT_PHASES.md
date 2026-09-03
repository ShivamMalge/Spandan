# IMPROVEMENT_PHASES.md — from 70 to the ceiling, by Sep 5

Written Sep 2, 22:30 IST. Deadline Sep 5. Detector frozen unless Phase E's
go/no-go says otherwise. Every phase ends at a gate whose acceptance criteria
are commands, not claims — same convention as `PHASES.md`.

## The target, stated honestly

Goal is 9/10 on every axis of the scorecard. **Eight of ten axes can get there
by Sep 5. Two cannot, and this file says exactly why and what each would take.**

| # | Axis | Now | Reachable by Sep 5 | Ceiling after | Blocked by |
|---|---|---|---|---|---|
| 1 | Track fit | 9 | **10** | — | nothing |
| 2 | Detector performance | 5 | **6** (7 if Phase E ships) | 8–9 | the detector itself; see §E |
| 3 | Measurement rigor | 9 | **9.5** | — | nothing |
| 4 | FP cost & business framing | 7 | **9** | — | nothing |
| 5 | Engineering quality | 8 | **9** | — | nothing |
| 6 | Reproducibility & honesty | 9 | **9.5** | — | nothing |
| 7 | AI usage | 4 | **7** | 9 needs a thesis change; see §D | the project's own architecture |
| 8 | Demo & communication | 5 | **9** | — | nothing |
| 9 | Innovation | 8 | **9** | — | nothing |
| 10 | Completeness & polish | 6 | **9** | — | nothing |

Projected balanced score after Phases A–F: **~85**. After Phase E ships (gated,
see below): **~88**. The two axes that stay under 9 are the two where the only
honest path is more time than exists, and the doc treats them that way.

**Time budget.** Sep 3 and Sep 4 full days (~8 focused hours each), Sep 5 until
the submission. ~20 working hours. Phases are ordered zero-risk first, so if
anything slips, what is lost is the risky work, not the safe work.

---

## Phase A — Framing and communication (Sep 3 morning, ~3.5h, zero risk)

**Goal.** A judge with ten minutes finds the rubric mapping, the shippable
configuration, and the AI story without deriving any of them. Nothing in this
phase touches code or figures; every number used already exists.

**In scope.**

1. **The shippable operating point** (README, after the Results table; and
   FAILURE_MODES §0.1). The project currently ends its results on a
   disqualification. Add the recommendation every risk write-up ends with:

   > *Inline blocking is not deployable at any alert budget (1 in 71). The
   > configuration this project would ship is **alert-only at budget 2**: event
   > precision 0.696, precision 0.2034 at a 0.15% base rate, 2.1 alerts/day,
   > 35 of 60 episodes caught, and 0.37% of legitimate traffic flagged — 1 in
   > 271. One analyst reviewing two items a day catches 58% of campaigns.*

   Keep the caveat the project already wrote: alert-only invalidates both
   halves of the rupee model without a response-time model. State it in the
   same paragraph, not below the fold. Figures are all in the existing frontier
   table — nothing is re-measured.

2. **Track 2 rubric map** (README, new short section after "The problem"). Six
   lines: each clause of the stated bar → where it is met → the test or command
   that proves it. *Detector for one class of loss → card testing, gen/ASSUMPTIONS
   §1.7. Held-out test set → temporal split, `test_loader_rejects_nontemporal_split`.
   Measured precision/recall → results table, `make eval`. FP cost → rupee model,
   break-even ₹613. Defense-only → reserved-range identifiers,
   `test_all_identifiers_synthetic`.*

3. **AI-usage section** (README, one paragraph, titled plainly "Where AI is,
   and where it is forbidden"). What the LLM does (explains a flag to an
   analyst), what it is structurally prevented from doing (poisoned-import
   test), what the measurement found (fabrication, §8), what shipped instead
   (the template), and — after Phase D — the validator that catches it.
   Written as an engineering result, not an apology.

4. **Industry anchor for 1-in-71.** One sourced figure for card-not-present
   false-decline rates, cited inline in FAILURE_MODES "What a flag does" and
   the README. If no citable figure can be found in 30 minutes, write "no
   public figure was found; the comparison is to practitioner reports" and
   stop — an uncited number is worse than none.

5. **Prune prep material out of the repository.** `docs/INTERVIEW_PREP.md`
   (3,507 lines, the largest file in the repo) and `docs/PITCH_CHECKLIST.md`
   are candidate preparation, not deliverables. Move both to a directory
   outside the repo, or to `prep/` with a `.gitignore` entry. `RESEARCH.md`
   and `spandan-brief.md` stay: they are provenance and were already
   de-pitched.

6. **Executive summary block** at the very top of the README, above "The
   problem": four lines a judge reads in fifteen seconds — what it is, the two
   headline numbers, the shippable configuration, the one measured failure.

**Explicitly out of scope.** Any change under `python/spandan/detect/`,
`spandan-core/`, or to any measured figure.

**Acceptance criteria.**

```
git diff --stat -- python/spandan/detect spandan-core   # must be EMPTY
python scripts/check_figures.py                          # PASS (Phase B lands this; until then, scratchpad version)
grep -c "budget 2" README.md docs/FAILURE_MODES.md       # >= 1 each
grep -n "Track 2" README.md                              # rubric map present
git ls-files docs/ | grep -c "INTERVIEW_PREP\|PITCH_CHECKLIST"   # expect 0
```

**Effort.** 3.5h. **Risk: none.** Gains: axis 1 → 10, axis 4 → 8.5, axis 8 → 7.

---

## Phase B — Hygiene, CI, and the missing test (Sep 3 afternoon, ~3h, low risk)

**Goal.** Nothing a judge's linter, CI, or a Linux laptop would find in the
first minute. And close the class of defect the audit found: no test in this
project reads a number out of a Markdown file.

**In scope.**

1. **Delete the dead code.** `python/spandan/eval/harness.py:695` and `:740`
   define `render_multiseed` and `render_verdict`, which are silently shadowed
   by redefinitions at `:941` and `:983` (ruff F811). Delete the first pair
   (~90 unreachable lines). Then `ruff check --fix` for the three unused
   imports and the unused variable in `cli.py:191`. The harness is not the
   detector; the freeze does not apply.

2. **`scripts/check_figures.py` — commit the figure checker.** The scratchpad
   script that verified the audit fixes becomes a repository artifact: reads
   `data/metrics.json`, greps every figure the README and FAILURE_MODES assert,
   fails on mismatch. Wire it as `make check` and into CI. This is the audit's
   lesson turned into a guard, and it is the single most on-brand addition
   available: the seventh instance of the pattern was documentation, and this
   makes the eighth impossible.

3. **CI.** `.github/workflows/ci.yml`: ubuntu-latest, Python 3.11, stable
   Rust; `make setup`, `pytest -q` on a reduced marker or the fast subset,
   `cargo test --release`, `ruff check`. Full `make eval` is too slow for CI;
   run the parity and generator tests, which are the ones that guard claims.
   A green badge is worth a point on its own; the fresh-clone bug in
   `BUILD_LOG` entry nine would have been caught on the first push.

4. **`make bench` platform guard.** `bench.py` imports `ctypes.wintypes` at
   module scope and dies on Linux/macOS. Guard the import and the memory
   counters behind `sys.platform == "win32"`; on other platforms print the
   throughput tables and state that RSS measurement is Windows-only rather
   than crash. Not the detector; freeze does not apply.

5. **Housekeeping.** Delete the `docs/*.md.bak` files from disk (they are
   gitignored but a fresh-clone reviewer never sees them; a local one does).
   Move `agents.md` to `docs/agents.md` and update the README link — a
   house-rules file at repo root reads as noise to a judge. Remove
   `scripts/notimpl.py` and the Makefile comment that references it.

**Acceptance criteria.**

```
ruff check python/ tests/                                # 0 errors
cargo clippy --manifest-path spandan-core/Cargo.toml --release   # clean
git diff --stat -- python/spandan/detect spandan-core   # EMPTY
make eval > /tmp/after.txt && diff <(grep -v seconds /tmp/before.txt) <(grep -v seconds /tmp/after.txt)   # IDENTICAL
python scripts/check_figures.py                          # PASS
pytest -q && cargo test --release                        # 95 + 33
gh run list --limit 1                                    # CI green on GitHub
```

The `make eval` diff is the gate that matters: deleting dead code from the
harness must not change one byte of output.

**Landed (Sep 3).** The shadowed `render_multiseed`/`render_verdict` pair and
three unused imports are gone from the harness; ruff reports 0 errors across
`python/`, `tests/` and `scripts/`. `scripts/check_figures.py` is committed and
wired as `make check` (31 figures across 13 documents, PASS) and into `make all`.
`.github/workflows/ci.yml` runs two parallel jobs on ubuntu: `test` (setup, ruff,
clippy, cargo test, pytest, graph render) and `figures` (`make data`, `make eval`,
`make check`). That second job is a deviation from item 3 above, which had ruled
`make eval` too slow for CI: in its own job it costs wall time only, and it turns
the fresh-clone reproduction claim into a machine check on every push. `make bench`
is guarded behind `MEMORY_SUPPORTED`, with a test that fakes the platform after
numpy has loaded (numpy reads `sys.platform` at import). `agents.md` moved to
`docs/`, `scripts/notimpl.py` removed, stray `.bak` files deleted.

The gate held: `make eval` before and after the harness deletion diff identical
with timing lines excluded, and `metrics.json` identical field for field. 119
Python tests pass (the 118 plus the bench guard); clippy clean; the detector diff
is empty.

CI went green at eb9aa3f (run 33710648936: `test` 4 min, `figures` 10 min)
after three red runs, each a real finding. First, the workflow installed an
unpinned ruff and 0.16 enabled rules 0.15.21 does not, three of them inside the
frozen `reference.py`; pinned. Second, the bench-guard test faked win32 on a
Linux host and called counters that do not exist there; the live half now runs
only on Windows. Third, and the one worth the detour: the parity fixture is
exact to the Windows C runtime, and under glibc 640 of its 3,866 scores move
by up to 2.8e-14 (BUILD_LOG, 2026-09-03, entry 13). The fixture tests compare
within the 1e-9 the fixture declares off Windows. The `figures` job passed on
every run, red or green: `make data`, `make eval` and `make check` reproduce
all 31 documented figures on ubuntu, and Rust-versus-Python parity on Linux
held at zero delta. Job logs need admin rights through the API; the pytest
step now re-emits its failure summary as annotations, which do not.

**Effort.** 3h. **Risk: low** — the harness edit is a deletion, and the eval
diff catches any accident. Gains: axis 5 → 9, axis 6 → 9.5, axis 10 → 8.

---

## Phase C — The learned baseline (Sep 4 morning, ~4h, low risk)

**Goal.** Test the core design claim — that six hand-weighted terms are enough —
against a learned model on the same features. Whichever way it comes out, it
is a result. Detector untouched.

**In scope.**

1. **Feature extraction, read-only.** `python/spandan/eval/features.py`: run
   `ReferenceDetector` over warm-up + validation + test exactly as the harness
   does, and for every event capture the six term values from the `evidence`
   dict `_score` already returns, plus `amount_paise`, `declined`, and
   hour-of-day. Output arrays aligned with the split. No change to
   `reference.py`; this reads what it computes.

2. **Two models, selected on validation only.** Logistic regression on the six
   terms (same features, learned weights — the cleanest test of the claim), and
   a small gradient-boosted model on the nine features (does richer
   combination help). `scikit-learn` becomes a dev dependency, never a runtime
   one. Threshold for each chosen under the **same** alerts/day ≤ 10 constraint
   on validation, so the comparison is like for like.

3. **Report through the existing pipeline.** Same three precisions, same rupee
   model, same per-control false-positive table, same multi-seed. New section
   FAILURE_MODES §9 "Learned weights versus hand weights", and one row per
   model in the README results table.

4. **Say what it means, whichever way.** If the hand-weighted detector holds
   within the multi-seed range, the design is vindicated with evidence. If the
   learned model wins on precision at the realistic base rate, say so, state
   by how much, and note it as the fix that would ship next — it does not
   unfreeze anything, because the learned model is reported, not shipped.

**Explicitly out of scope.** Replacing the detector's scoring with the learned
model. Any change to `reference.py` or the Rust core. Feature engineering beyond
the nine listed — the point is the comparison, not a leaderboard.

**Acceptance criteria.**

```
git diff --stat -- python/spandan/detect spandan-core   # EMPTY
python -m spandan.eval.features --data data --out data/features.npz
python -m spandan.eval.baselines --data data            # prints the comparison table
pytest tests/test_baselines.py -v
  #   test_features_are_read_from_detector_evidence_not_recomputed
  #   test_baseline_threshold_selected_on_validation_only   (poisoned test split, like the harness's)
  #   test_baseline_never_sees_labels_at_feature_time
python scripts/check_figures.py                          # PASS after the doc update
```

**Landed (Sep 3).** `spandan.eval.features` reads the six terms off
`ReferenceDetector._advance` and records the detector score, asserted identical
to the harness pass on all 805,066 test events; `spandan.eval.baselines` fits
logistic regression on the six terms and a gradient-boosted model on nine, on
the warm-up window, thresholds on validation under the same budget, three seeds,
through `evaluate_scored` — the harness tail split out so there is one copy of
the pipeline (`make eval` byte-identical before and after). `make baselines`
writes `data/baselines.json`; `make check` verifies every quoted figure from it;
the CI figures job runs it. Six tests, including the poisoned test split, the
label-blind extraction, and the hand row reproducing `evaluate` field for field.
scikit-learn is a dev extra; `spandan.detect` is asserted not to import it.

**Measured.** Precision at the 0.15% base rate does not move across seeds
(logreg6 median 0.0845 vs hand 0.0824, ranges overlapping; gbm9 0.0696). Recall
does: 0.9228 vs 0.8444 median at the same alert budget, the linear model's worst
seed above the hand weights' best; net roughly doubles. Neither learned model
fixes the single-merchant outage (45.1% and 66.5% flagged vs 50.5%). The linear
model's weights are a real criticism of the hand weights (BIN velocity and
decline excess ×0.12, per-IP velocity sign-flipped, repetition damping ×5).
Reported in FAILURE_MODES §9 and a README table; nothing shipped, detector
untouched. CI: the first Linux `figures` run failed on the boosted row alone.
First read as a platform effect (BUILD_LOG entry 14); the Phase G fresh clone
on the same machine reproduced the Linux figures and showed the cause was the
scikit-learn version, 1.8.0 in the working venv against 1.9.0 everywhere fresh
(entry 15). scikit-learn is pinned to 1.9.0, the boosted row is re-quoted from
the pinned run with the 1.8.0 figures marked superseded, and `make check` is
exact for every row again.

**Effort.** 4h. **Risk: low-medium** — the risk is spending time on the GBM;
if it runs over, ship the logistic regression alone, which is the comparison
that matters. Gains: axis 3 → 9.5, axis 7 → 5.5, axis 9 → 8.5.

---

## Phase D — The AI layer earns a number (Sep 4 afternoon, ~3h, low risk)

**Goal.** Convert the fabrication finding from "found and reported" to "found,
fixed, and measured." FAILURE_MODES §8 names the unbuilt fix: a validator that
rejects any explanation referencing evidence outside the `Flag`. Build it.

**In scope.**

1. **`llm/grounding.py` — schema-grounded validation.** Given a note and the
   rendered prompt it was generated from (exactly what the model saw), reject
   it if it references a field or concept the pipeline does not carry: a
   curated deny-list for the fields that do not exist (CVV, AVS, 3DS, reason
   code, cardholder IP, per-card history, geography, MCC) plus a check that
   every rupee amount and percentage in the note appears in the prompt.
   Validation lives inside `explain_flag`, so no caller can obtain an
   unvalidated note. Rejection falls back to the template — the same
   degradation `explain` already has for a cassette miss — with exit code 4
   and a one-line reason; the rejected note goes to stderr for the record.

2. **Measure it, do not assert it.** Run the validator over both committed
   cassettes: it must reject both (they are the fabrication finding). Then,
   because the recording provider changed to the Anthropic API (`claude-haiku-4-5`)
   between the finding and this phase, record **two pairs**, not one, so the
   two variables are not confounded: the *plain* prompt on the new model (does
   a different model family fabricate?) and the *grounded* prompt on the new
   model (does enumerating what does not exist help?). Six cassettes, six
   verdicts, in a table in §8, whatever the counts are. If the grounded prompt
   still fabricates, that is the result, and it strengthens the boundary
   argument.

3. **Tests.** `test_validator_rejects_the_recorded_fabrications` (the two
   committed cassettes must fail), `test_validator_accepts_the_template`
   (the deterministic note must pass), `test_validator_cannot_see_labels`,
   and the existing `no_network` fixture applies to all of them.

4. **Update TARGET.md and §8** with the validator's results. The AI story in
   the README (Phase A item 3) gets its last sentence.

**Explicitly out of scope.** Any LLM involvement in scoring — the poisoned-import
test must stay green and is re-run as an acceptance criterion. Tool use, agent
loops, a second provider.

**Acceptance criteria.**

```
env -u ANTHROPIC_API_KEY pytest tests/test_llm.py -v     # all pass, sockets blocked
pytest tests/test_llm.py::test_eval_runs_with_llm_import_poisoned   # STILL GREEN
python -m spandan.cli explain --flag-id txn_000804993    # exit 4 (validator rejection), template shown
python -m spandan.cli validate-cassettes                 # one verdict per cassette, with reasons
ls python/spandan/llm/cassettes/ | wc -l                 # 6: 2 gemini plain, 2 haiku plain, 2 haiku grounded
```

**Effort.** 3h. **Risk: low.** Gains: axis 7 → 7, axis 9 → 9.

**Why axis 7 stops at 7, in writing.** This is an "AI Risk Manager" track and
this project's thesis is that the AI is kept out of the decision path. A judge
scoring "how central and effective is the AI" will not give 9 to a system whose
AI explains and never decides, however well the boundary is proven. Reaching 9
on this axis means either the learned model from Phase C beating the hand
weights *and being shipped as the detector*, or an LLM component that measurably
improves an analyst outcome — both of which change the thesis and neither of
which fits before Sep 5. The honest path to 9 is post-submission: ship the
learned model if Phase C shows it wins, with the same evaluation discipline.

---

## Phase E — The measured fix (Sep 4 evening, ~3h experiment; ship only on go/no-go)

**Goal.** FAILURE_MODES §7 diagnoses the worst failure to the mechanism — retry
structure is invisible at a 5-minute window — and names the fix, a
long-horizon window on the BIN axis. Measure the fix. Do not ship it unless
the gate says so.

**Part 1 — the experiment (always do this, ~3h).**

1. `python/spandan/detect/experimental.py`: a `LongHorizonDetector` that
   **subclasses** `ReferenceDetector` and adds a second BIN-axis window (60
   minutes) feeding only the `repetition` term. `reference.py` is not
   modified; the freeze holds. The Rust core is not touched.

2. Run it through the full harness as a named variant (the ablation machinery
   already supports variants). Report in FAILURE_MODES §7: outage control flag
   rate, precision at the realistic base rate, legitimate-decline rate, and
   the multi-seed range — beside the frozen detector's figures.

3. Whatever the number is, it is the result. "The diagnosed fix moves the
   outage false-positive rate from 50.5% to X%" is a far stronger sentence than
   "the fix is diagnosed," and it costs nothing to the frozen detector's
   evidence.

**Part 2 — the go/no-go for shipping it (decide Sep 5 morning, do not start
before).**

Ship the long-horizon window as the detector **only if all four hold**:

- outage_single_merchant flag rate falls below **15%** (from 50.5%), and
- precision at the 0.15% base rate rises above **0.15** (from 0.0824), and
- legitimate-decline rate falls below **0.7%** (1 in 140, from 1 in 71), and
- it is before **10:00 on Sep 5**, leaving six hours for the Rust port, parity
  fixture regeneration, bit-exact re-verification, a full `make eval` on both
  engines, and regeneration of every figure in every document.

If any one fails: **do not ship.** Report the experiment in §7 and leave the
frozen detector as the submission. A half-ported fix with a broken parity
claim is worth less than a frozen detector with a measured unbuilt fix.

**Acceptance criteria (Part 1).**

```
git diff --stat -- python/spandan/detect/reference.py spandan-core   # EMPTY
python -m spandan.eval.harness --data data --seeds 3 --variant long_horizon
pytest tests/test_experimental.py -v
  #   test_long_horizon_reduces_to_reference_when_window_disabled
  #   test_long_horizon_scores_match_reference_on_first_five_minutes
```

**Registered (Sep 3), before any run on the test window.**
`spandan.detect.experimental.LongHorizonDetector`: a 60-minute BIN-axis window
feeding the `repetition` term only; weights 1.2 (hand) and 6.0 (five times, the
section 9 linear model's warm-up multiplier for this term); thresholds on
validation under the same budget; three seeds; run as `--variant` through the
full harness beside the frozen detector (`make experiment`). The four gate
conditions above are unchanged.

**Measured (Sep 3).** Outage flagged 50.5% → 35.6% at the hand weight, → 18.0%
at five times it; precision at the 0.15% base rate 0.0824 → 0.1071 → 0.1658;
legitimate declines 1 in 71 → 1 in 95 → 1 in 166; recall 0.8444 → 0.8413 →
0.7969. **Gate: do not ship.** The hand weight fails all three numeric
conditions; the five-fold weight passes two and fails the outage condition
(18.0% vs 15%). Reported in FAILURE_MODES §7 with the per-scenario and
three-seed tables; the detector stays frozen; Part 2 does not start. The
first run crashed in the seed matrix (a config nested inside itself) and the
eval gate caught a wrong focus row in the default rendering; both fixed, both
tested, `make eval` byte-identical on the final harness. CI green at c327dd9
(run 33744743775): all three jobs, the experiment regenerated on ubuntu and
checked against §7.

**Effort.** 3h for the experiment. **Risk: low** for Part 1 (a subclass that
can be deleted). **Risk: high** for Part 2, which is why it has a gate and a
clock. Gains: axis 2 → 6 from Part 1 alone; → 7 if Part 2 ships.

**Why axis 2 stops at 6–7, in writing.** The detector is measured against a
control that has 50.5% of its events flagged and a headroom of −267%. Even a
successful long-horizon window is one term in a six-term hand-weighted score
with no reason codes, no 3DS, no AVS, and a synthetic stream that
`ASSUMPTIONS.md` §2 says flatters precision. A 9 on this axis means a detector
that separates the hardest control cleanly at a realistic base rate. That is a
research result: reason codes in the schema, a second window, learned weights,
and validation against real traffic. It is weeks, not days, and it is the
correct next project.

---

## Phase F — Demo and pitch (Sep 5 morning, ~2h, zero risk)

**Goal.** A judge who never clones the repository still sees it work.

**In scope.**

1. **A 60–90 second recording.** `spandan replay --data data --limit 20000`
   scrolling flags with the rupee exposure counter; then the engine-swap diff
   (`make eval ENGINE=rust` vs `python` differing in one line); then
   `spandan explain` showing the validator reject a fabrication and fall back
   to the template. Terminal only; asciinema or a GIF. Embed at the top of the
   README under the executive summary.

2. **A pitch outline** — not a checklist, the actual five-minute structure —
   in `docs/PITCH.md`: the two headline numbers, the shippable configuration,
   the measured failure and the measured fix, the boundary proof, the learned
   baseline result. Every slide is a number that `make eval` prints.

3. **One screenshot** of the frontier table and one of a flag with its six
   contributions, committed under `docs/img/`, referenced from the README.

**Acceptance criteria.**

```
ls docs/img/                                  # recording + two screenshots
grep -n "docs/img" README.md                  # embedded
python scripts/check_figures.py               # PASS — the pitch quotes no figure the check does not cover
```

**Effort.** 2h. **Risk: none.** Gains: axis 8 → 9, axis 10 → 9.

---

## Phase G — Verify and submit (Sep 5 afternoon, ~2h)

The Phase 6 acceptance run, repeated, with the new checker in the chain:

```
git clone . <fresh> && cd <fresh>
python -m venv .venv && . .venv/Scripts/activate
python -c "import sys; print(sys.executable)"          # the clone's venv
make setup && make all                                 # numbers match README
python scripts/check_figures.py                        # PASS
pytest -q && cargo test --release                      # green
git status --porcelain ; echo "porcelain exit=$? (EMPTY)"
gh run list --limit 1                                  # CI green
```

Then a final BUILD_LOG entry for anything Phases A–F broke and fixed, and
submit with hours to spare. **Nothing new starts on Sep 5 after 14:00.**

**Verified (Sep 3, on c327dd9, before Phase F).** Fresh clone into a new
directory, its own venv, `make setup` from the dev extras (maturin resolved from
the extras, not the global PATH), then the full `make all` chain: stream
regenerated, 130 tests passed on the clone, `make eval`, `make baselines`,
`make experiment`, `make demo`, and `make check` PASS with 431 figures across 13
documents against the clone's own build. `cargo test --release`: 33 passed.
`git status --porcelain` empty afterwards. The clone's `metrics.json` is identical to the working repository's, engine label aside;
its `baselines.json` and both experiment JSONs are byte-identical to the
repository's once the repository was re-run under the pinned scikit-learn 1.9.0
(the clone had resolved 1.9.0 on its own, which is how the version effect of
BUILD_LOG entry 15 came to light).
CI green on the same commit in all three jobs (run 33744743775). The run was
interrupted once by the tool shell dying mid-chain, not by the build; the
remaining stages were resumed in the same clone from the first missing output,
and the stages that had completed were not repeated. The chain is to be run
once more after Phase F lands, before submission.

---

## Phase H — The triage graph (Sep 3–4, ~7h, low risk to the detector, new artifact)

**Why this phase exists.** `docs/RESEARCH.md`, Sep 2 addendum: the reported
criteria reward *Failure Recovery* (a graceful fallback, shown) and *AI Judgment*
(deterministic where AI is unnecessary; LLM-decides-money is marked down); the
public field shows post-detection decision layers presented as artifacts with
audit trails and stopping rules. Spandan has every piece of such a layer and no
object that *is* the layer. This phase builds it as an explicit, deterministic
graph — nodes are functions, edges are routing functions, the LLM node is
leaf-only behind the validator, and the diagram is rendered from the
declaration so it cannot drift.

**Goal.** A judge can point at one module and one diagram and see: what
happens after a flag, why, in what order, where a human is interrupted, where
the system stops itself, and that the LLM cannot reach an action. And one
number that did not exist before: the outage control's legitimate-decline rate
*with the kill-switch*, printed by `make eval` beside the raw one.

**In scope.**

1. **`python/spandan/triage/graph.py` — the declaration.** A typed `TriageState`
   (the frozen `Flag`, the running per-(merchant, BIN) counters, the decision so
   far, the audit entries). Nodes as plain functions `state -> state`:
   `dedup` (15-min cooldown per merchant+BIN — already `metrics.alerts` logic,
   now a node), `budget_gate` (alerts/day), `exposure` (rupee at risk from the
   existing cost model), `kill_switch` (below), `mode` (inline-decline vs
   alert-only from the operating point), `explain` (the LLM node), `ground`
   (the validator), `template`, `human_review` (an interrupt: the state is
   written and execution stops), `act`, `audit`. Edges as an explicit table of
   `(node, routing_fn)`; `compile()` validates at import: every edge target
   exists, every node is reachable from START, every path reaches END, and
   **no path exists from `explain` to `act`** — the boundary as topology.
   No framework: nodes are functions, the runtime stays numpy-only.

2. **The audit record, written before the action.** Every node transition
   appends one JSON line — flag id, node, decision, reason, inputs it read,
   timestamp from the event stream (not the wall clock, so the trail is
   byte-identical across runs) — to `data/audit.jsonl`. `act` refuses to run
   unless the entry for its own decision is already on disk. Deterministic
   and replayable, like everything else here.

3. **The kill-switch.** Per (merchant, BIN): not a decline count — a count
   trips on every burst too. The trailing-hour **attempts per distinct card**
   is the retry structure §2.2 shows separates an outage from a probe run at
   sixty minutes and not at five. Measured on TRAIN only before anything on
   test was read: attacks at or below 1.65, `outage_single_merchant` at
   3.68–5.41. Registered in `costs.toml [operations]` as
   `kill_switch_retry_ratio = 2.5` over `kill_switch_min_events = 20`, holding
   alert-only for `kill_switch_cooldown_ms = 3600000`, with the basis written
   beside it. Graceful degradation aimed at the measured worst failure: during
   a single-merchant issuer outage the detector keeps scoring, stops declining,
   and says so. A **routing** change; `reference.py` and the Rust core are
   untouched.

4. **Measure it.** The harness gains `--triage`: scores are unchanged, but
   "declined" is computed through the graph. `make eval` prints, beside the
   existing rows, the legitimate-decline rate and the outage control's flagged
   *and declined* counts with the kill-switch, and the number of trips. Report
   in FAILURE_MODES §2.1 as a routing result, explicitly not a detector result,
   the way §0.1 reported the constrained threshold beside the unconstrained.

5. **Render from the declaration.** `python -m spandan.triage.graph --mermaid`
   emits the diagram; `scripts/check_figures.py` (Phase B) also diffs it
   against the block in the README so the picture cannot drift from the code.

**Explicitly out of scope.** Any LLM routing decision. Any new action beyond
decline / alert / hold-for-review. A dashboard. A queue. A framework
dependency. Any change to scoring.

**Acceptance criteria.**

```
git diff --stat -- python/spandan/detect spandan-core             # EMPTY
pytest tests/test_triage.py -v                                   # 17 tests
  #   test_graph_compiles_and_every_node_is_reachable
  #   test_no_path_from_explain_to_act              <- the boundary as topology
  #   test_llm_node_has_exactly_one_outgoing_edge_into_ground
  #   test_mermaid_is_rendered_from_the_declaration  <- diagram == edge table
  #   test_audit_entry_exists_before_act_runs
  #   test_audit_trail_is_byte_identical_across_runs
  #   test_kill_switch_trips_on_retry_structure_and_not_on_distinct_cards
  #   test_kill_switch_holds_alert_only_for_the_cooldown_then_releases
  #   test_alert_only_mode_declines_nothing
  #   test_human_review_interrupt_halts_and_persists_state
  #   test_dedup_follows_the_alert_cooldown
  #   test_explain_is_optional_and_grounding_gates_the_note
  #   test_triage_never_changes_a_score              <- scores in == scores out
  #   test_triage_package_does_not_import_the_llm_layer
  #   test_no_node_reads_labels_or_scenario_ids
  #   test_config_loads_registered_parameters_from_costs_toml
pytest tests/test_llm.py::test_eval_runs_with_llm_import_poisoned   # now runs the triage pass under poison
make eval 2>&1 | sed -n "/^TRIAGE/,/^This is a ROUTING/p"          # the new block
spandan triage-graph --mermaid > /tmp/g.mmd && grep -c "explain --> ground" /tmp/g.mmd   # 1
python scripts/check_figures.py                                   # PASS
```

**Measured (Sep 3).** 20 trips, all on `outage_single_merchant`, none on any
attack; attack declines unchanged (TP through action 9,038 → 9,038); false
declines 11,216 → 7,769; legitimate-decline rate 1 in 71 → **1 in 102**. A 31%
recovery, late by construction; reported in FAILURE_MODES §2.1a as a routing
result. The graph, audit and topology tests shipped; 17 tests; poisoned-import
test extended to run the graph under poison.

**Effort.** ~7h. **Risk: low to the detector** (untouched, asserted), **medium
to the schedule** — this displaces Phase C and Phase E if time is short (see
the cut list). Gains: Failure Recovery from "in BUILD_LOG" to "measured, with a
switch"; AI Judgment from a test to a topology; Build Quality (a tested
artifact); axis 8 (the demo has a centrepiece). Score after: ~86.

**Why this outranks the learned baseline (Phase C) with two days left.** The
reported criteria name failure recovery and AI judgment; neither names a
baseline comparison. A technical panel will still ask for the baseline, and
`docs/AUDIT.md` still lists it as the top exposure — but a judge scoring the
published criteria gets more from a measured kill-switch than from a logistic
regression. If both fit, do both. If one must go, C goes.

## Schedule

| When | Phase | Hours | Risk | Score after |
|---|---|---|---|---|
| Sep 3 AM | A — framing | 3.5 | none | 74 |
| Sep 3 PM | B — hygiene, CI, checker | 3 | low | 77 |
| Sep 3 PM–Sep 4 AM | **H — triage graph** (D part 1 is done) | 7 | low | 83 |
| Sep 4 PM | C — learned baseline *(if time)* | 4 | low | 86 |
| Sep 4 eve | E part 1 — measured fix *(if time)* | 3 | low | 87 |
| Sep 5 AM | F — demo (the graph and the audit trail are the centrepiece) | 2 | none | 88 |
| Sep 5 PM | G — verify, submit | 2 | none | — |

**If time runs short, cut from the bottom of this list, never from the top:**
E part 2 first, then E part 1, then C entirely, then H's kill-switch measurement
(keep the graph, the audit trail and the topology tests — those are the
artifact). Phases A, B and F are never cut — they are the cheapest points in the
plan and they carry no risk to what is already green. H's graph itself is not
cut once started: a half-built triage layer is worse than none, so H is either
completed to its acceptance criteria or reverted in one commit.

## What this plan will not do, and why

- **Will not unfreeze the detector without the Phase E gate.** Every number in
  every document rests on the freeze; breaking it without six clear hours for
  regeneration converts a verified submission into an unverified one.
- **Will not add an LLM to the decision path to score higher on the AI axis.**
  The poisoned-import proof is worth more than the points, and it would be a
  lie about what this project is.
- **Will not re-run `make bench` again.** The figures are dated and the drift is
  documented; a third run just produces a third set of numbers to explain.
- **Will not claim 9/10 on axes 2 and 7 by Sep 5.** The path to both is written
  above. It runs past the deadline, and saying so is worth more than pretending
  otherwise — which is, after all, the whole argument of the repository.
