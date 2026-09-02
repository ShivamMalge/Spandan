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
   because the recording provider changed to Groq (`llama-3.3-70b-versatile`)
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
env -u GROQ_API_KEY pytest tests/test_llm.py -v          # all pass, sockets blocked
pytest tests/test_llm.py::test_eval_runs_with_llm_import_poisoned   # STILL GREEN
python -m spandan.cli explain --flag-id txn_000804993    # exit 4 (validator rejection), template shown
python -m spandan.cli validate-cassettes                 # one verdict per cassette, with reasons
ls python/spandan/llm/cassettes/ | wc -l                 # 6: 2 gemini plain, 2 groq plain, 2 groq grounded
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

---

## Schedule

| When | Phase | Hours | Risk | Score after |
|---|---|---|---|---|
| Sep 3 AM | A — framing | 3.5 | none | 74 |
| Sep 3 PM | B — hygiene, CI, checker | 3 | low | 77 |
| Sep 4 AM | C — learned baseline | 4 | low | 80 |
| Sep 4 PM | D — validator | 3 | low | 83 |
| Sep 4 eve | E part 1 — measured fix | 3 | low | 84 |
| Sep 5 AM | E part 2 go/no-go, then F — demo | 2 (+6 if E ships) | high if E ships | 85 (88) |
| Sep 5 PM | G — verify, submit | 2 | none | — |

**If time runs short, cut from the bottom of this list, never from the top:**
E part 2 first, then E part 1, then D's second pair of cassettes, then C's GBM
(keep the logistic regression). Phases A, B and F are never cut — they are the
cheapest points in the plan and they carry no risk to what is already green.

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
