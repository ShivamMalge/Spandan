# Audit — Spandan

Read-only audit performed 2026-08-30 against commit `553f543`. Nothing in the
repository was changed. Every finding below carries the command output or code
that establishes it.

**Method.** `make eval` was re-run from a clean state and every quantitative
claim in `README.md`, `docs/*.md`, `python/spandan/gen/ASSUMPTIONS.md`,
`python/spandan/llm/TARGET.md` and code comments was compared against it.
`make bench` was re-run. Card-novelty shares were recomputed directly from
`data/*.jsonl.gz` rather than read from `manifest.json`. Markdown links, cited
test names and cited commit SHAs were resolved programmatically. Git history was
scanned for secrets with `git log -p --all`.

**Headline.** The engineering claims hold. The *numbers* in two documents do
not: `docs/FAILURE_MODES.md` §1/§2.1/§6 and `python/spandan/gen/ASSUMPTIONS.md`
§1.7/§1.7a/§1.7b carry figures from a superseded run, and `docs/BENCH.md`'s
streaming and memory figures do not reproduce on the machine that produced them.
The worst measured failure in the project is **worse** than documented (50.5%
against a published 39.9%), so every correction moves against the project.

---

## BLOCKER

### B1 — `FAILURE_MODES.md` §2.1: every figure in the headline-failure table is stale

**Where.** `docs/FAILURE_MODES.md:287–293`, and the prose at `:299`, `:304`,
`:317`, `:326`.

**Evidence.** Doc says:

```
At the constrained operating point (threshold 23.05):
| `flash_sale`              | volume        | 17,787  |     4 | 0.0002 | ₹0     |
| `issuer_outage`           | decline ratio | 18,057  | 1,573 | 0.0871 | ₹1.79L |
| `outage_single_merchant`  | ...no crutches| 18,169  | 7,240 | 0.3985 | ₹8,595 |
| `benign`                  | —             | 740,349 |    84 | 0.0001 | ₹29,139|
```

`make eval` (exit 0) produces:

```
control                    events  flagged    rate   blocked-good cost   axis
flash_sale                  17787       20  0.0011                ₹350   volume
issuer_outage               18057     1818  0.1007              ₹2.03L   decline ratio
outage_single_merchant      18169     9170  0.5047             ₹11,077   decline ratio, no crutches
benign                     740349      208  0.0003             ₹37,847   ordinary traffic
```

Every row differs. The prose figure **39.9%** (`:299`) is now **50.5%**; the
count 7,240 is 9,170; headroom "−250%" (`:304`) is `-267.4%` per the run.

**Why it matters.** This is the project's headline failure and the section a
reviewer is directed to from the README. The README now says 50.5% while this
file says 39.9% — a reviewer opening both finds two numbers for the same fact
and cannot tell which is current. The file's own header (`:7`) reads *"State:
final (Phase 6) … the numbers measured then are the final numbers"*, which
asserts currency the table does not have.

**Smallest fix.** Replace the four table rows and the four prose figures with
the `make eval` values above. No re-measurement needed; the run output is the
source.

### B2 — `FAILURE_MODES.md` §2.1 contradicts itself on the threshold, three ways

**Where.** `docs/FAILURE_MODES.md:287`, `:304`, `:340` — against `:148`.

**Evidence.**

- `:287` "At the constrained operating point (threshold **23.05**)"
- `:304` "scores 80.79 against a threshold of **23.05** — headroom −250%"
- `:340` "scores **80.79** against a threshold of **21.15**"
- `:148` (§0.1, correct) "| threshold | 21.43 | **21.99** |"

`make eval` prints `threshold 21.99` and `headroom -58.80 (-267.4% of the
threshold)`.

**Why it matters.** Three different thresholds for one operating point inside
one document, none of which is the actual one. This is the single most likely
thing for a careful reviewer to notice, because §0.1 and §2.1 sit in the same
file and disagree.

**Smallest fix.** Set all three to 21.99 and the headroom to −267.4%.

### B3 — `FAILURE_MODES.md` §6: the rupee breakdown is from a superseded run

**Where.** `docs/FAILURE_MODES.md:544–545`, `:549`.

**Evidence.** Doc: *"Gross ₹2.82L on the headline seed: avoided chargeback
exposure ₹5.59L, saved authorization fees ₹13,962, blocked good transactions
−₹2.91L"* and *"11,800 of the 13,947 blocked clean transactions"*.

`make eval`:

```
saved authorization fees                   ₹13,557
avoided chargeback exposure                 ₹5.37L
blocked good transactions                  ₹-2.52L
GROSS (before review cost)                  ₹2.99L
of 11216 blocked clean transactions, 9397 were going to decline anyway
```

**Why it matters.** §6 is the cost-model sensitivity section; the break-even
₹613 that the README quotes is derived from this gross. The ₹613 itself is
correct in the current run, so the section's *conclusion* survives — but its
inputs as printed do not reproduce.

**Smallest fix.** Replace the five figures with the run values.

### B4 — `FAILURE_MODES.md` §1: `slow_low` event recall is wrong

**Where.** `docs/FAILURE_MODES.md:264`.

**Evidence.** Doc: *"Its event-level recall is still the lowest of the three at
**0.267**."* `make eval` per-scenario table:

```
slow_low                     1147      510   0.4446   recall
burst                        4693     4514   0.9619   recall
rotating                     4864     4014   0.8252   recall
```

0.4446, not 0.267. It remains the lowest of the three, so the sentence's claim
holds; the number does not.

**Smallest fix.** 0.267 → 0.445.

### B5 — `ASSUMPTIONS.md` §1.7: the realised flash-sale mixture is misstated

**Where.** `python/spandan/gen/ASSUMPTIONS.md:140–145`.

**Evidence.** Doc: *"The **realised** known-customer share on the shipped
dataset is **57.5%** … 57.5% known / 42.5% new is a genuine mixture"*.

`data/manifest.json` records `flash_sale_known_customer_share: 0.437199`.
Recomputed directly from the shipped stream rather than trusting the manifest:

```
scenario                   distinct cards  seen in benign  NEVER seen
flash_sale                           9968          43.7%      56.3%
```

**Why it matters.** ASSUMPTIONS opens with *"Every metric this project reports
is downstream of this file"*, and §1.7 calls this *"the single most load-bearing
choice in the file"*. The stated figure is 14 percentage points off, and the
same sentence explains the gap by *"a 14-day window"* — the shipped stream is
100 days.

**Smallest fix.** 57.5% → 43.7%, 42.5% → 56.3%, and drop "in a 14-day window".

### B6 — `ASSUMPTIONS.md` §1.7a: the table justifying the binding constraint is wrong

**Where.** `python/spandan/gen/ASSUMPTIONS.md:172–176` (the novelty table), and
`README.md:161–162` which repeats it.

**Evidence.** Doc claims cards never seen elsewhere: attacks 100%, `flash_sale`
**~42%**, outages **~10%**. Recomputed from the shipped stream:

```
burst                    100.0% never seen
rotating                 100.0% never seen
slow_low                 100.0% never seen
flash_sale                56.3% never seen      <- doc says ~42%
issuer_outage              0.9% never seen      <- doc says ~10%
outage_single_merchant     0.9% never seen      <- doc says ~10%
```

**Why it matters.** These three percentages are the entire stated justification
for §1.7a, the binding no-card-novelty constraint, which is in turn the
justification for the `Axis` enum having no `Card` variant. The conclusion is
unaffected — the gap between 100% and 56.3% still makes novelty a partial free
separator, so the ban is still warranted — but the numbers a reviewer would
check are wrong, and the outage figure is off by a factor of eleven.

**Smallest fix.** ~42% → 56.3%, ~10% → 0.9%, in both ASSUMPTIONS and README.

### B7 — `ASSUMPTIONS.md` §1.7b: a table headed "Measured on the shipped stream" was not

**Where.** `python/spandan/gen/ASSUMPTIONS.md:196–204`.

**Evidence.** Doc versus `data/manifest.json` (`negative_case.issuer_outage`):

| Property | ASSUMPTIONS §1.7b | manifest / shipped stream |
|---|---|---|
| Decline ratio | 82.4% | 0.824824 → 82.5% |
| Distinct BINs | 4 (one per episode) | 22 |
| Attempts per card | **5.36** | **12.343** |
| Median amount | ₹1,664 | 165886 paise → ₹1,658.86 |
| Known-customer share | **89.9%** | **99.1%** |
| burst attempts/card | 1.58 | 1.573 |
| burst median amount | ₹26 | 3027 paise → ₹30.27 |

**Why it matters.** The table is explicitly labelled "Measured on the shipped
stream" and it was not measured on the shipped stream — it is from the 14-day
predecessor. Attempts-per-card and known-customer share are the two rows the
document calls "the separators available to the detector".

**Smallest fix.** Regenerate the table from `manifest.json`, which already holds
every value.

### B8 — `BENCH.md` and `README.md` streaming and memory figures do not reproduce

**Where.** `docs/BENCH.md:84–91` (streaming), `:101`, `:106–109` (memory);
`README.md:43` and `README.md:121–123`.

**Evidence.** Fresh `make bench` (exit 0) on the same machine:

| figure | published | fresh run | drift |
|---|---|---|---|
| rust streaming ev/s | 120,053 | **199,925** | +66% |
| rust p99 | 24.80µs | **11.60µs** | −53% |
| python streaming ev/s | 21,766 | **37,191** | +71% |
| python p99 | 119.30µs | **52.40µs** | −56% |
| rust bytes/entity | 3,874 | **4,819** | +24% |
| python bytes/entity | 1,975 | 1,971 | −0.2% |
| rust monthly projection | 31.0 GB | **38.6 GB** | +25% |
| rust churn RSS | 2,058 MB | **2,560 MB** | +24% |
| full-stream entities | 10,819 (at 200k events) | 12,662 (at 300k events) | see H2 |

The batch table reproduces acceptably (28.55µs → 29.45µs, 18.90 → 19.00). The
derived ratios are close but not equal: "5.5×" is 5.38×, "4.8× better p99" is
4.52×, "twice the memory per entity" is 2.44×.

**Why it matters.** `README.md:43` puts `120,053 events/s / 24.8µs` in the
headline results table, and `README.md:121` states the trade sentence with
`3,874 vs 1,975 bytes` and `31 GB vs 16 GB`. A reviewer who runs `make bench`
gets different numbers. Timing drift is expected and BENCH.md does say the
figures are from one machine — but the **memory slope is not a timing artifact**
and moved 24%, and the entity count is deterministic (531,282 both runs) so the
per-entity byte figure is a real change, not noise.

**Smallest fix.** Either re-run `make bench` once and publish that run's numbers
with the date, or state in both files that the figures are a single run on named
hardware and will not reproduce exactly. The second is cheaper and is already
half-written in BENCH.md's preamble.

### B9 — `README.md` claims every figure is reproduced by `make eval`; several are not

**Where.** `README.md:82`.

**Evidence.** *"Every figure in this file is reproduced by `make eval` on a
fresh clone."* The README's throughput, p99, bytes-per-entity and GB-per-month
figures are produced by `make bench`, not `make eval`, and per B8 they do not
reproduce. Confirmed by grep: `120,053`, `24.8`, `3,874` appear nowhere in
`make eval` output.

**Why it matters.** It is a blanket reproducibility claim, and it is the
sentence a skeptical reviewer would test first. It is false for exactly the
figures that cannot reproduce.

**Smallest fix.** Narrow it: "Every figure in the results table above is
reproduced by `make eval`; the throughput and memory figures come from
`make bench` and are single-machine measurements."

### B10 — a test's docstring claims an enforcement it does not perform

**Where.** `tests/test_gen.py:252–275`.

**Evidence.**

```python
banned = ("first_seen", "novel_card", "card_novelty", "unseen_card", "new_card")
root = Path(spandan.__file__).parent
for path in root.rglob("*.py"):
    if "gen" in path.parts:
        continue
    text = path.read_text(encoding="utf-8").lower()
    offenders.extend(f"{path.name}:{token}" for token in banned if token in text)
```

The docstring says: *"This test fails the moment a module starts tracking
first-seen cards."* It does not. It fails only if a module contains one of five
hardcoded substrings. A feature named `seen_before`, `is_returning`,
`known_card` or `card_age` passes. It also scans `*.py` only — no Rust file is
examined.

**Mitigating fact, stated so the severity is not overread.** The property
itself *is* substantiated, by a different and genuinely strong test:
`tests/test_reference_detector.py:229 test_no_card_novelty_state_is_retained`
renames every card to an unseen value and asserts `np.array_equal` on the score
arrays. That is a behavioural guard and it is sound. The Rust side is guarded
structurally by `Axis` having no `Card` variant.

**Why it matters.** It falls in the category this repo's own BUILD_LOG calls the
plausible-number pattern: an artifact that looks like enforcement and is not.
The claim is in the docstring, not in the code's behaviour.

**Smallest fix.** Reword the docstring to what the test does — "fails if a
module names a card-novelty concept using one of these tokens; the behavioural
guard is `test_no_card_novelty_state_is_retained`" — and add a pointer to that
test. No code change.

---

## HIGH

### H1 — `make bench` cannot run on Linux or macOS

**Where.** `python/spandan/eval/bench.py:25` and `:75`.

**Evidence.** `import ctypes.wintypes` at module scope, and
`ctypes.WinDLL("kernel32", use_last_error=True)` in `_memory_counters()`.
`ctypes.wintypes` is Windows-only; importing it elsewhere raises. There is no
platform guard and no fallback.

**Scope, checked.** `bench.py` is imported by nothing else —
`python/spandan/eval/__init__.py` exports only `costs` and `loader` symbols, and
no test imports it. So `make test`, `make eval` and `make all` are unaffected.
Only `make bench` breaks.

**Why it matters.** `README.md:60` says "Requires Python ≥3.10 and a Rust
toolchain" and `:64` offers a POSIX activate path, then `:73` documents
`make bench` with no platform caveat.

**Smallest fix.** One sentence in the README and BENCH.md: `make bench` requires
Windows.

### H2 — `BENCH.md` states experiment parameters the code no longer uses

**Where.** `docs/BENCH.md:38` and `:101` against
`python/spandan/eval/bench.py:278`.

**Evidence.** BENCH.md: *"200,000 real events"* and *"Full stream (200k events,
10,819 entities)"*. The code: `parser.add_argument("--limit", type=int,
default=300_000)`. Fresh run prints `benchmark stream: 300,000 events from
data/train.jsonl.gz` and `300,000 events, 12,662 entities`.

**Why it matters.** The documented setup does not describe the experiment the
command runs, so a reviewer reproducing it is measuring something else.

**Smallest fix.** 200,000 → 300,000 and 10,819 → 12,662 in BENCH.md.

### H3 — the flash-sale guard does not run against the shipped data, which would fail it

**Where.** `tests/test_gen.py:167–169`, fixture at `:46–48`,
`tests/helpers.py` `SMALL_CONFIG`.

**Evidence.**

```python
known = len(flash_cards & benign_cards) / len(flash_cards)
assert known > 0.50, f"only {known:.2%} of flash-sale cards are known customers"
assert known < 0.95, f"{known:.2%} known leaves too few first-time customers"
```

The `built` fixture calls `build(SMALL_CONFIG, out)` — a 4-day stream with two
flash-sale episodes. The shipped 100-day dataset measures **43.7%**, which is
below this test's own lower bound of 0.50. Nothing asserts the property against
`data/`.

**Why it matters.** This is the guard added in response to the Zipf bug
(`BUILD_LOG.md:41`), the project's most-cited catch. It now passes on a fixture
while the dataset every published number comes from would fail it. Whether 43.7%
is acceptable is a judgement call the project has not made in writing —
`ASSUMPTIONS.md` argues the sale must "sit in between", and it still does, but
outside the bound the project itself chose.

**Smallest fix.** Documentation only, given the freeze: state the shipped
realised share (B5) and say explicitly whether 43.7% is still considered inside
the intended band. Adding a manifest-based assertion is a code change and is out
of scope before submission.

### H4 — a docstring reports the known share as if it were the novel share

**Where.** `tests/test_gen.py:255`.

**Evidence.** *"Attack cards are 100% novel; flash-sale cards are only ~43%
novel."* 43.7% is the share that **is** known (previously seen); the novel share
is 56.3%. The sentence inverts its own quantity.

**Why it matters.** It is inside the test that enforces the binding constraint,
and it makes the sale look less novel than it is — the direction that would make
the constraint seem less necessary.

**Smallest fix.** "~43% novel" → "~56% novel".

### H5 — internal strategy documents are tracked and would be published

**Where.** `docs/RESEARCH.md`, `docs/spandan-brief.md`.

**Evidence.** `docs/RESEARCH.md:1` — *"# Winning Submission Design — Razorpay AI
Buildathon 2026, Track 2 … (for Shivam / ShivamMalge)"*. `:14` — *"Typical
student submissions are trivially differentiable … Shivam wins simply by doing
the opposite"*. `:28` — *"**Why this wins.**"* The file also reasons about which
Razorpay product lines have gaps.

**Why it matters.** The user states this repository goes public. These are
planning documents about how to impress the panel, not engineering documents.
They are not inaccurate and they are not dishonest — but they change what the
repository reads as, and a reviewer encountering "why this wins" and an analysis
of their employer's product gaps is reading something written for a different
audience. `README.md:275` links `docs/` as a whole; the two files are reachable.

**Smallest fix.** A judgement call for the author, not a correctness fix. Either
leave them (they are candid and the project's ethos is disclosure) or untrack
them. Flagging only so the decision is deliberate.

---

## MEDIUM

### M1 — `BUILD_LOG.md` misstates the test bound it introduced

**Where.** `docs/BUILD_LOG.md:69` against `tests/test_gen.py:168`.

**Evidence.** Doc: *"the test now asserts the mixture from both sides (`0.55 <
known < 0.95`)"*. Code: `assert known > 0.50`.

**Smallest fix.** 0.55 → 0.50 in BUILD_LOG.

### M2 — the detector constants exist in two places with no test asserting agreement

**Where.** `python/spandan/detect/interface.py:88–120` and
`spandan-core/src/score.rs:67–86`.

**Evidence.** Both define all fourteen defaults. They currently agree —
`window_ms 300_000`, `ring_capacity 512`, `baseline_sample_interval_ms 60_000`,
`baseline_min_samples 20`, `ewma_halflife_samples 30.0`, the six weights,
`threshold 3.0`, both ablation flags. `tests/test_reference_detector.py:268`
pins the Python side only. No test compares the two.

**Scope, checked.** `python/spandan/detect/rust_engine.py:73` constructs the
native detector with `**_config_kwargs(self.config)`, passing every field
explicitly, so `Default` is not on the parity path and drift could not break the
parity tests. It would silently change behaviour for anyone using the Rust crate
directly.

**Smallest fix.** None before submission; the risk is latent and the detector is
frozen. Worth one sentence in `score.rs` noting the Python file is authoritative.

### M3 — superseded figures in `PHASES.md` review-pass sections are not marked superseded

**Where.** `docs/PHASES.md:862`, `:871–872`.

**Evidence.** *"Precision at a realistic base rate now leads the report —
0.0956"*, *"Threshold 21.15 → 23.05, precision at target 0.0693 → 0.0956,
break-even ₹204 → ₹985"*. Current values are 0.0824, threshold 21.99, break-even
₹613.

**Why it matters.** These are dated historical review records and are legitimate
as history — but the word "now" and the absence of any superseded marker means a
reviewer skimming can read 0.0956 as current. The project marks superseded
figures elsewhere (`FAILURE_MODES.md:157`), so the inconsistency is in the
convention's application.

**Smallest fix.** One line at the top of the review-pass history: "figures in
this section are as they stood at each gate and are superseded by
FAILURE_MODES.md".

### M4 — orphaned script and a Makefile comment that no longer describes the Makefile

**Where.** `Makefile:5`, `scripts/notimpl.py`.

**Evidence.** The header comment reads *"Targets whose phase has not been handed
over exit non-zero via scripts/notimpl.py rather than silently succeeding."* No
target invokes it — `grep -rn "notimpl"` matches only this comment and the file
itself. All targets are implemented.

**Smallest fix.** Delete the sentence, or the file, or both. Cosmetic.

### M5 — `ASSUMPTIONS.md` §2.2 still describes the 14-day stream

**Where.** `python/spandan/gen/ASSUMPTIONS.md:284`.

**Evidence.** *"Volume is stationary across the 14 days apart from the diurnal
and weekend terms."* The shipped stream is 100 days (`ASSUMPTIONS.md:24` states
100 days in the same file).

**Note.** The other "14 days" mentions at `:39` and `:45` are deliberate
historical references to the predecessor stream and are correct in context. This
one describes the current stream.

**Smallest fix.** 14 days → 100 days.

---

## LOW

### L1 — two test names cited in `PHASES.md` do not exist under those names

`docs/PHASES.md:280` cites `test_pr_auc_matches_sklearn_reference`; the test is
`tests/test_eval.py:125 test_average_precision_matches_bruteforce_reference`,
and its docstring at `:128` explains the rename ("PHASES.md named this
`test_pr_auc_matches_sklearn_reference`; scikit-learn is…"). `PHASES.md:286`
cites `test_no_card_novelty_feature_in_detector`; the test is
`test_no_card_novelty_feature_exists_anywhere`. Both renames are traceable; the
plan document was not updated. All other cited test names resolve, and all cited
commit SHAs resolve.

### L2 — `PHASES.md` acceptance commands use `/tmp` paths

`docs/PHASES.md:297`, `:436–437`, `:576` use `/tmp/...`. These do not exist on
the Windows machine the project is developed on; the commands are illustrative
rather than runnable as written. Harmless, but a reviewer copying them on
Windows gets an error.

### L3 — `docs/BUILD_LOG.md:50` names a test that no longer exists

*"(then named `test_flash_sale_reuses_benign_entities`)"*. This is explicitly
flagged as the old name in the sentence itself. Correct as written; listed only
because an automated name check flags it.

---

## Categories with no findings

Stated explicitly rather than padded.

- **Secrets.** No API key, token or credential in the working tree or in
  `git log -p --all`. Searched for `sk-ant-`, `sk-or-v1-`, `AIzaSy`, bearer
  tokens, PEM headers. Only environment-variable *names* appear
  (`GEMINI_API_KEY` at `provider.py:85`, `SPANDAN_LLM_MODE` at `:62`), which is
  correct — there is no `.env` and no dotenv loader.
- **Cassette contents.** Both committed cassettes carry only synthetic
  identifiers (BINs `099813`, `012399`, both MII-0; merchants `mer_004`,
  `mer_008`). No 13–19 digit runs anywhere in either file. `recorded_via` names
  the provider and exact model.
- **Broken links.** All markdown links in all 12 tracked `.md` files resolve.
- **Unseeded randomness.** None. All generation flows from
  `np.random.SeedSequence(cfg.seed)` at `build.py:49`. No `time.time()`,
  `datetime.now()`, `uuid4`, or bare `random.` in tracked Python.
- **Hardcoded absolute paths.** None in tracked source. No `C:\Users`, no
  `/home/`, no `/Users/` outside the `/tmp` examples in PHASES (L2).
- **Unused configuration.** Every non-`*_basis` key in `costs.toml` is read by
  `costs.py`. The `*_basis` keys are documentation by design.
- **Stale TODOs.** No `TODO`, `FIXME`, `HACK` or `XXX` in any tracked file.
- **Skipped or silently uncollected tests.** 95 collected, 95 run. One
  conditional `pytest.skip` at `tests/test_eval.py:83`; executed directly it
  passes rather than skipping, so it is not hiding a gap. No Rust `#[ignore]`.
- **Test counts as claimed.** README says 95 Python and 33 Rust.
  `pytest --collect-only -q` → `95 tests collected`. `cargo test --release` →
  29 + 4 + 0 doctests = 33.

---

## Non-numeric claims: what substantiates each

| Claim | Substantiated by | Verdict |
|---|---|---|
| "bit-exact" parity | `tests/test_parity.py:23 TOLERANCE = 0.0` with `assert delta <= TOLERANCE`; `np.array_equal` at `:62`, `:76`; Rust side `spandan-core/tests/parity.rs` against fixture tolerance 1e-9 | **Substantiated.** Cross-engine demands exact equality; the fixture allows 1e-9 and achieves 0e0. No contradiction between the two figures. |
| Committed fixture is 3,866 events | `json.load` of `tests/fixtures/parity.json` → 3866 events, tolerance 1e-09, no `label`/`scenario_id` keys | **Substantiated.** |
| "bounded memory" | `test_window_memory_bounded_per_entity`; the claim is qualified everywhere as *per entity*, with linear-in-entity-count stated in `BENCH.md:95` and `state.rs:5` | **Substantiated and correctly qualified.** The unqualified phrase does not appear. |
| "never touches the test set" | `tests/test_eval.py:95 test_threshold_selection_never_touches_test_window` — replaces the test split with a `Poisoned` list whose `__iter__`/`__getitem__` raise, then runs selection | **Substantiated.** Stronger than a call-graph check. |
| Budget registered before test was read | `git log -S "alerts_per_day_budget" -- costs.toml` → `e5b48f8`, 2026-08-24 19:42; SHA resolves | **Substantiated.** |
| "runs fully offline" / replay never hits network | `tests/test_llm.py` autouse `no_network` fixture replaces `socket.socket` for every test in the module; `provider.py:63` raises `CassetteMiss` rather than falling through | **Substantiated at the OS boundary.** |
| No evaluation number passes through an LLM | `test_eval_runs_with_llm_import_poisoned` (full eval twice, poisoned vs clean, `np.array_equal` on both arrays plus equal threshold); import-graph test | **Substantiated.** |
| Deterministic evaluation | Two `make eval` runs four days apart, diffed: **IDENTICAL** | **Substantiated.** |
| "reproduces from a fresh clone" | Performed this session at `bbce950`: clone, in-clone venv, `make setup && make all`, 95 passed twice, 33 Rust, `git status --porcelain` empty | **Substantiated for `make eval` figures. NOT substantiated for `make bench` figures** — see B8/B9. |
| Byte-identical generated stream | `test_seed_reproducible_byte_identical`; gzip `mtime=0` | **Substantiated.** |
| No card-novelty feature | `test_no_card_novelty_state_is_retained` (rename all cards, assert identical scores) + `Axis` has no `Card` variant | **Substantiated by the behavioural test.** The grep test that *claims* to enforce it does not — B10. |

---

## Must be fixed before submission

Four edits, all documentation, no code, no re-measurement:

1. **B1 + B2 + B3 + B4** — `FAILURE_MODES.md` §1, §2.1, §6. Paste in the
   `make eval` values. This is one editing pass over roughly fifteen figures in
   one file, and it removes the README-vs-FAILURE_MODES contradiction on the
   project's headline failure.
2. **B5 + B6 + B7** — `ASSUMPTIONS.md` §1.7, §1.7a, §1.7b, and the two
   percentages repeated at `README.md:161–162`. Values are in `manifest.json`
   and in the recount printed above.
3. **B9** — narrow the reproducibility sentence at `README.md:82` so it does not
   cover the `make bench` figures.
4. **B10** — reword one docstring at `tests/test_gen.py:255–262` to say what the
   test checks, and point at the behavioural guard. No code change.

**B8 needs a decision, not necessarily an edit.** Publishing a fresh `make bench`
run is one command; alternatively, one sentence in README and BENCH.md stating
these are single-run, single-machine figures resolves the honesty problem
without re-measuring. Either is defensible. Leaving both files asserting
`120,053 ev/s` and `3,874 bytes/entity` as reproducible is not.

## Leave alone until after submission

- **H1** (`make bench` is Windows-only) — one README sentence if there is time;
  it does not affect `make all` and no reviewer is likely to run `make bench` on
  Linux inside a two-day window.
- **H3** (the flash-sale guard does not cover shipped data) — the honest fix is a
  manifest-based assertion, which is a code change against a frozen component.
  Document the realised share (B5) and stop there.
- **H5** (strategy documents are public) — a judgement call, not a defect.
- **M2** (duplicated Rust constants) — latent, off the tested path, and the
  detector is frozen.
- **M1, M3, M4, M5, L1, L2, L3** — accurate-but-sloppy. None would change a
  reviewer's assessment; all are one-line edits whenever convenient.

**What this audit did not find.** No secret, no leaked credential, no broken
link, no unseeded randomness, no dead configuration, no skipped test hiding a
gap, no absolute path, and no case where the code fails to do what the
architecture documents say it does. The defects are concentrated in one place:
figures that were true when written and were not regenerated after the run that
produced them was superseded. That is the same failure class `BUILD_LOG.md`
documents five times — a plausible artifact with nothing currently behind it —
appearing here in the documentation rather than in a measurement.
