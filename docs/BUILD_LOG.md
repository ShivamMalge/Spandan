# BUILD_LOG.md

Append-only (`agents.md` §9). Every bug that cost more than ten minutes gets an
entry, written when the bug is fixed rather than at the end of the phase. Bugs
caught in review get entries too.

Entry format:

```
## <date> — <one-line symptom>
**Phase:** <n>
**Symptom:** what was observed.
**First believed:** the wrong diagnosis, stated plainly.
**Actually wrong:** the real cause.
**Fix:** what changed.
**Proved by:** the command whose output showed it fixed.
```

## 2026-08-24 — `pip install -e .` failed: maturin could not find `python/spandan_core`
**Phase:** 0
**Symptom:** `python -m pip install -e '.[dev]'` died in metadata generation:
`python-source is set to .../python, but the python module at .../python/spandan_core
does not exist.` The Rust crate itself compiled fine; the failure was in maturin's
PEP 517 hook, before any linking.
**First believed:** a `module-name` typo, or that `abi3-py310` needed a Python 3.10
interpreter present (3.11.9 is what is installed).
**Actually wrong:** neither. In maturin's mixed rust/python layout, the compiled
extension must live *inside* a package directory under `python-source` — a top-level
`module-name = "spandan_core"` with no `python/spandan_core/` directory is not a
valid arrangement. The abi3 tag and the interpreter version were never involved.
**Fix:** built the extension as the private submodule `spandan_core._native`
(`module-name = "spandan_core._native"`, `#[pymodule] fn _native`) and added
`python/spandan_core/__init__.py` re-exporting its surface, so callers still write
`import spandan_core` as PHASES.md Phase 0 and Phase 4 specify.
**Proved by:** `maturin develop --release` → `🛠 Installed spandan-0.1.0`, then
`python -c "import spandan_core as s; print(s.__version__, s._smoke_add(2,3))"` →
`0.1.0 5`.
**Note for Phase 4:** this is the first of the two PyO3 packaging gotchas PHASES.md
expects; the zero-copy NumPy one is still ahead.

## 2026-08-24 — the benign flash sale was separable by entity novelty alone
**Phase:** 1
**PITCH-VIDEO CANDIDATE** — the "what broke" beat. The point of the story is not
the bug, it is that the test which caught it asserted a property of the *output*
rather than checking that the code did what it was written to do. A code-mirroring
test would have passed forever and the leak would have surfaced in Phase 2 as
suspiciously good precision, which is the hardest kind of error to notice because
it looks like success.
**Symptom:** `test_flash_sale_is_a_mixture_of_known_and_new_customers` (then named
`test_flash_sale_reuses_benign_entities`) failed: only **66%** of flash-sale cards
had ever appeared in benign traffic, against an expected >90%.
**First believed:** an off-by-something in the test — the flash-sale generator does
draw its cards from the benign pool, so overlap "should" have been total.
**Actually wrong:** the generator was correct about the *pool* and wrong about the
*distribution*. Benign traffic draws cards Zipf(1.35)-weighted, so most of the
9,000-card pool never transacts; the flash sale drew **uniformly** over the same
pool, so roughly a third of its customers were identifiers that appear nowhere else
in the stream. The negative case was therefore partly separable by novelty alone —
"many unseen cards" would have distinguished sales from attacks for free, and the
only false-positive test in the project would have been measuring the wrong thing.
This would not have shown up as a failure anywhere downstream; it would have shown
up as suspiciously good precision in Phase 2.
**Fix:** flash-sale entities are now an explicit mixture — 75% known customers drawn
with the *same* popularity weights as benign traffic, 25% genuinely new customers
with unseen cards, IPs and devices (`flash_sale_new_entity_fraction`). Both halves
are deliberate: all-known would make novelty a free separator one way, all-new the
other way. Recorded in `gen/ASSUMPTIONS.md` §1.7 as the most load-bearing choice in
the generator.
**Fix commit:** the test now asserts the mixture from both sides (`0.55 < known <
0.95`) and additionally asserts that attack cards share nothing with benign cards.
**Proved by:** `python -m pytest tests/test_gen.py -v` → 15 passed, including
`test_flash_sale_is_a_mixture_of_known_and_new_customers`.
**Worth noting:** the test that caught this was written to measure a property rather
than to check that the code did what it was written to do. A test asserting "flash
sale cards come from the benign pool" would have passed.

## 2026-08-24 — the replay demo flagged the issuer outage; `make eval` did not
**Phase:** 2
**Symptom:** `spandan replay --limit 25000` printed flags on BIN 009064 across
mer_002/004/005/006 — mean amount ₹2,602 against a baseline of ₹2,568, 77% declined,
28 distinct cards in a 29-event window. That is the issuer-outage control, and the
running exposure counter never moved, confirming label 0. Meanwhile `make eval`
reported FP=0 on the same test window with the same threshold.
**First believed:** a threshold mismatch between the two paths, or the CLI reading a
stale `metrics.json`.
**Actually wrong:** neither. `make eval` warms the detector on the full training
window before scoring test; the CLI started from an empty state machine. With cold
per-entity baselines the BIN's baseline window count sits near 1.0 with almost no
variance, so an ordinary outage window scores 17-21 standard deviations above it.
The eval was right and the demo was misleading — the worst way round, because the
demo is what a reviewer watches.
**Fix:** `spandan replay` now warms on `train.jsonl.gz` before replaying, matching
the eval exactly, and a `--cold-start` flag shows the cold behaviour deliberately.
**Proved by:** warmed — 230 flagged, 230 truly card testing, zero false positives.
`--cold-start` — 29 flagged, 24 attack, 5 issuer_outage false positives.
**The finding underneath the bug:** cold start is a real failure mode, not just a
demo artifact. A detector deployed against a new merchant, or restarted, has no
per-entity baseline and will over-flag exactly the traffic that most resembles an
attack. It is now in `docs/FAILURE_MODES.md` with the numbers, and `--cold-start`
exists so it can be demonstrated rather than described.
## 2026-08-24 — the threshold sweep, not the detector, was 80% of `make eval`
**Phase:** 2 (addendum)
**Symptom:** after the stream grew from 14 days to 100, a single-seed `make eval`
took **12m30s**. The obvious suspect was the detector: 1.6M events at ~25k events/s
is 64 seconds a pass, and the run makes four passes.
**First believed:** scoring dominated and the only fix was Phase 3's Rust core.
**Actually wrong:** scoring was ~256s of the 750s. The threshold sweep was ~100s
*per call* and there are four of them. The sweep evaluates 60 operating points, and
each one re-walked the candidate events in Python — `metrics.alerts` building a
dict per flagged event and `compute_costs` doing per-event attribute access. At
~51k candidates that is ~3M dict constructions and ~3M attribute walks per sweep,
for arithmetic that is pure array work.
**Fix:** `metrics.SweepPrecompute` derives the arrays once (scores, labels,
amounts, approved-flags, and a (merchant, BIN) group code, all sorted by group then
time). `alert_count_at` then finds alert boundaries with two vector comparisons —
a flagged event starts a new alert when its predecessor is in a different group or
more than the cooldown earlier — and `costs.gross_at` reduces the cost model to
masked sums.
**Proved by:** sweep time **100s -> 0.36s** for 60 thresholds, a ~280x reduction,
with exact agreement against the per-event loops at every threshold checked:
```
  th=    2.10  alerts vec= 34104 loop= 34104   gross vec=-622,325,158.9 loop=-622,325,158.9
  th=   59.24  alerts vec=     7 loop=     7   gross vec=     442,278.1 loop=     442,278.1
  th=  112.57  alerts vec=     1 loop=     1   gross vec=         150.0 loop=         150.0
```
**Kept honest by:** `test_vectorised_sweep_agrees_with_the_per_event_loops` checks
12 thresholds on every run. The per-event loops are still the reference and are
still what `evaluate` uses for the final reported numbers; the vectorised path is
only the sweep. A fast path that quietly disagrees with the reference would be
worse than a slow one.
**Worth noting for Phase 4:** the instinct to blame the slowest-looking component
was wrong by a factor of three. That is the argument for measuring before
optimising, and it is the same discipline the Rust-vs-NumPy benchmark will need.

## 2026-08-25 — two findings withdrawn on our own initiative: the pattern, not the incidents
**Phase:** 2 (addendum) and the frontier fix
**PITCH-VIDEO CANDIDATE — and this entry is the frame, not either individual bug.**

Two results were reported, then retracted after further measurement. Neither was
caught by a reviewer noticing an error; both were withdrawn because a check we ran
ourselves showed the claim could not carry the weight put on it.

**Retraction 1 — "the per-entity EWMA baseline is not carrying the signal."**
Reported from a single stream, where dropping EWMA improved net position by
₹17,159. Re-run across three seeds, the delta was ₹12,981 median with a range of
[−₹43,496, +₹109,443], changing sign across streams. The finding was withdrawn.
The opposite finding — that EWMA is vindicated — was **also refused**, because the
same data cannot support it: full has the higher median but loses on one seed of
three. The honest result is a null one, and it is reported as a null one.

**Retraction 2 — "constraining the operating point improved precision 0.400 →
0.487."** Measured on a 60-point threshold sweep. At 600 points the constrained
pick inside the same budget moves to a different threshold with higher validation
net that generalises worse, and the real improvement is 0.418 → 0.446. The coarse
grid had happened to place the pick at a favourable threshold. Superseded figures
are marked as superseded in `docs/FAILURE_MODES.md` rather than quietly swapped,
so the diff shows what changed and why.

**Why this is the pattern worth showing.** Both retractions came from the same
habit: when a number is load-bearing, measure it a second way before believing it —
more seeds, a finer grid, a property asserted about the output rather than the
code. Three of this project's four significant corrections were found that way (the
third being the Zipf flash-sale leak, which a property-asserting test caught and a
code-mirroring test would have passed forever).

The submission's argument is evidence over claims. A project that argues that and
never withdraws anything has not tested the claim. These two are the evidence that
it was applied to its own results, including the flattering ones.

**Proved by:** `make eval` prints the paired per-seed ablation deltas with the
verdict "NOT consistent across seeds", and the 600-point frontier alongside the
superseded 60-point figures.

## 2026-08-25 — the first memory benchmark measured nothing and looked plausible
**Phase:** 4
**Symptom:** every RSS column in the first `make bench` run read `0.0MB` — peak
RSS "0 MB", full-stream growth "0.0MB" for both engines. The throughput tables
around them were correct, so the output as a whole looked credible enough to
paste into BENCH.md.
**First believed:** that Windows working-set accounting simply was not visible
from Python without a new dependency, and the memory section might need psutil.
**Actually wrong:** two stacked ctypes mistakes. `ctypes.windll.psapi.GetProcessMemoryInfo`
resolves on some Windows builds and fails on others (modern Windows exports it as
`K32GetProcessMemoryInfo` on kernel32) — and with no declared `argtypes`, the
process pseudo-handle from `GetCurrentProcess()` went through a default `c_int`,
truncating it on 64-bit. The call failed, the return value went unchecked, and
the zero-initialised struct was read as data.
**Fix:** `K32GetProcessMemoryInfo` via `WinDLL("kernel32")` with declared
`argtypes`/`restype`, `HANDLE` restype on `GetCurrentProcess`, and the return
value CHECKED — a failure now raises "RSS figures would be fiction" instead of
reporting zeros.
**Proved by:** the re-run measured 2,058MB growth for the Rust churn case and
exposed a real finding the zeros were hiding: the Rust engine costs ~2x the
memory per entity of the Python one (3,874 vs 1,975 bytes/entity).
**Worth noting:** a measurement that fails soft is worse than no measurement.
The zeros would have shipped inside a table whose other columns were honest,
which is the most credible possible disguise. The ok-check existed for one run
before it caught this; it earned its place immediately - same pattern as the
multi-seed check in Phase 2.

## 2026-08-25 — the batch=1 benchmark measured re-scoring one hot event
**Phase:** 4 (gate follow-up)
**Symptom:** none, and that is the point. 40,816 ev/s of per-event Python looked
like a strong, credible baseline. The Phase 4 gate applied the project's own
standard in the uncomfortable direction — an unexpectedly GOOD baseline deserves
the same suspicion as an unexpectedly good precision number — and asked for
verification. Note the incentive gradient: a slower honest baseline makes Rust
look better, so this error was flattering the comparison's loser and nobody
inside the project had a reason to catch it.
**First believed:** the number was real; CPython at ~25us/event over warm dicts
did not seem impossible.
**Actually wrong:** the benchmark re-scored the SAME chunk every repetition. At
batch=1 that measures one hot event against one warm entity - cache-resident
dicts, nothing evicting - and under-measures fresh-event work by ~2x (13.8us
same-chunk vs 29.8us fresh, verified directly). The tell was in the table
itself: batch=1 came out FASTER per event than batch=100k, and the streaming
bench, which does walk fresh events, disagreed at 46us. The same flaw distorted
the other engine the other way: repeated chunks stacked duplicate events into
windows, inflating Rust's mid-size per-event cost (8.6us -> 5.6us corrected).
**Fix:** every repetition walks a fresh, disjoint chunk. Corrected table in
docs/BENCH.md with the original marked wrong in both directions.
**Proved by:** batch=1 python 28.55us, now consistent with both the large-batch
rows (25-27us) and the streaming path; the faster-than-bulk anomaly is gone.
**The pattern, fourth and fifth instances:** a plausible artifact with nothing
behind it. (1) single-seed precision 1.00 - caught by the multi-seed check;
(2) bit-exact parity on a fixture that never filled a ring - caught by coverage
measurement; (3) 0.0MB RSS from an unchecked Win32 call - caught by checking
the return value; (4) this benchmark; (5) same session, a patch script wrote to
the wrong path variable, its verification grep returned empty, and the empty
result went unchecked - the same failure class as the unchecked psapi return,
in the build tooling instead of the measurement. Caught by the file-change
notices; patch scripts now assert every replacement and verify each file is
itself afterwards.

The real lesson across all five: **none were caught by staring harder at the
output.** Each was caught by a guard, a re-run under different conditions, or
someone asking whether the number should be true. That is the mechanism, and
the mechanism - not carefulness - is the claim. This is the walkthrough's
spine, not five separate anecdotes.

## 2026-08-26 — the recorded explanation cited card-verification fields that do not exist

The Phase 5 comparison was run against real wire output for the first time
(`gemini-3.1-flash-lite`, free tier, cassettes committed as returned). The
model's note for the ₹5.45 probe rested its entire decision rule on "the
CVV/AVS result on this attempt" — a field that exists nowhere in the `Flag`,
the prompt, or the pipeline. The ₹150 note conditioned a blacklist on per-card
history and a cardholder IP it was never given. The prompt explicitly said
"the evidence below is everything known"; the fabrication happened anyway.

Caught by reading the output against the schema — the same move as asking
whether a number should be true, applied to prose. Not caught by the format
checks (four parts, under 140 words, no score restated: all passed). A
well-formed note with invented grounds is the prose version of a plausible
figure with nothing behind it.

Two decisions followed. The cassettes stay as recorded — re-prompting until a
nicer sample appears and shipping that one is threshold selection on the test
set, again. And the shipped explainer is the deterministic template, which
cannot fabricate because substitution can only place fields that exist. The
comparison verdict is in `python/spandan/llm/TARGET.md` (the earlier
in-context comparison is marked superseded there, not swapped); the
architectural argument for why none of this can touch a number is
FAILURE_MODES §8.

Lesson: prompt discipline is not a boundary. The import graph is.

## 2026-08-26 — the socket-poison test passed only because of packages the project never declared

**Phase:** 6 (fresh-clone acceptance run)
**Symptom:** in the fresh clone with its own venv, `make all` failed:
`test_record_mode_without_key_still_never_reaches_the_network` died with
`TypeError: function() argument 'code' must be code, not str` raised from
*inside* `import ssl` — line 1006 of the stdlib, `class SSLSocket(socket):`.
The same suite had just passed 95/95 on the build machine.
**First believed:** a broken venv, or the record-mode test genuinely reaching
the network in the clean environment.
**Actually wrong:** an import-order dependency hidden by the build machine's
global site-packages. The `no_network` fixture replaces `socket.socket` with a
plain function for every test in the module. `ssl` subclasses `socket.socket`
at import time — so the process's *first* `import ssl` must not happen while
the poison is in place. On the build machine, globally installed pytest
plugins (langsmith → httpx → ssl) imported `ssl` during startup, long before
any fixture ran; the clone's venv declares none of them, so the record-mode
test's `import urllib.request` performed the first `import ssl` under the
poison and the class statement itself blew up. Reproduced on the build machine
with one variable: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_llm.py`
→ same TypeError.
**Fix:** `tests/test_llm.py` imports `urllib.request` at module top, before
any fixture patches socket, with a comment saying the import is load-bearing.
**Proved by:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_llm.py -q`
→ 10 passed, and again with plugins enabled → 10 passed.
**The pattern, and a bonus instance of it:** the fresh-venv-inside-the-clone
criterion was written to catch exactly this — a suite green because the global
environment happens to hold something the project never declares — and it
caught it on first contact. The bonus: the check script itself initially
reported exit 0 on this failed run, because its output was piped through
`tail` and the pipeline's exit code was tail's. An unchecked pipeline exit is
the same class as the unchecked Win32 return and the unchecked patch-script
grep; the re-run checks the script's own status directly.
