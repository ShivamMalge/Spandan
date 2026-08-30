# Design research — why card-testing, and why this architecture

Planning research written before any code existed. Kept in the repository for
provenance: it records what was surveyed, which directions were rejected and on
what evidence, and which constraints were fixed up front. Figures cited here are
as they stood at research time and were not re-verified afterwards; the measured
numbers for this project live in `docs/FAILURE_MODES.md` and `docs/BENCH.md`.

## TL;DR
- **Build a streaming fraud-spike detector for card-testing / velocity-anomaly attacks** — a deterministic Rust core (clean-room EWMA + sliding-window Welford + per-entity ring buffers) exposed via PyO3, with an LLM used only for bounded, cassette-tested triage explanations. This is the **least-crowded, most-defensible** Track 2 direction because Razorpay already ships RTO products (Thirdwatch → Magic Checkout, plus the FTX'26 "RTO Shield") and a chargeback product (the FTX'26 Dispute Responder Agent), but has **no merchant-facing card-testing/BIN-attack spike detector**.
- **Recommended title: Spandan** (Sanskrit "pulse/throb") — "the pulse-monitor that catches card-testing storms before the chargebacks land." It fits his Sanskrit naming convention and appears free of major fintech/PyPI/crates collisions (confirm the registry pages before publishing).
- **Deliverable is finishable solo in 12 days** because the hard novelty is methodological (honest temporal metrics + false-positive cost in rupees + Rust streaming latency numbers), not algorithmic — EWMA, sliding-window Welford, and ring buffers are each a day's clean-room work. Everything that was tempting but load-bearing on nothing (SIMD, custom hash maps, union-find ring linkage) is cut outright rather than parked as a stretch goal.

## Key Findings

**1. Razorpay's existing risk stack leaves card-testing wide open.** Razorpay's fraud/risk products cluster around two loss classes: (a) **RTO/return risk** — Thirdwatch (acquired August 2019), merged into Magic Checkout on 1 January 2023, analyzing 200-300+ order parameters in under 200 ms to red/green-flag COD orders, plus the FTX'26 "RTO Shield" (LLM address validation + bad-pincode intelligence) and "RTO Insights" agents; and (b) **chargebacks/disputes** — the Dispute Responder Agent launched at FTX'26 (12 March 2026) on the Claude Agent SDK, which auto-collects transaction evidence and submits before the deadline. **Neither addresses card-testing/BIN-attack spikes or transaction-velocity anomalies at the merchant level** — a distinct, real, costly loss class. That is the gap to target.

**2. The Indian fraud landscape makes velocity/spike attacks timely, and mule-ring detection is already crowded by the regulator.** UPI fraud reached **6.32 lakh cases worth ₹485 crore in H1 FY2024-25 (April–September 2024)** per CPFIR data disclosed by MoS Finance Pankaj Chaudhary in the Lok Sabha on 25 November 2024, with full FY24 at 13.42 lakh cases worth ₹1,087 crore (up ~85% from FY23's 7.25 lakh / ₹573 crore); a separate cyber-intelligence surveillance report (mFilterIt) flagged over 524,000 mule instances in March 2026 alone. Critically, **mule-account/abuse-ring detection is already heavily productized** — RBI's MuleHunter.AI (built by the Reserve Bank Innovation Hub, launched December 2024) had **23 banks implemented as of a December 2025 RTI (Medianama)**, with adopters including Canara Bank, Punjab National Bank, Bank of India, Bank of Baroda and AU Small Finance Bank; RBI CGM Suvendu Pati stated Canara Bank "has achieved an accuracy level of 95 percent in detecting mule accounts," while RBIH's own project page lists "AI-powered fraud detection with 85%+ accuracy." RBI's DPIP is also in pilot with 7 banks. Building "abuse-ring sentinel" as the core would compete head-on with a regulator's flagship. Card-testing spike detection, by contrast, is a merchant/acquirer-side problem those RBI tools do not cover.

**3. Typical student submissions are trivially differentiable.** The overwhelmingly common student/hackathon fraud project is: Kaggle `creditcardfraud` CSV (284,807 rows, 0.17% fraud, anonymized PCA features) → SMOTE → XGBoost → ~"100% accuracy" → Streamlit dashboard, evaluated with a **random** train/test split. This is exactly what a fintech risk panel dismisses: no temporal split, accuracy on a balanced/oversampled set, no false-positive cost, no latency story. The opposite of each of those — temporal split, PR-AUC, a rupee cost model, streaming latency, and stated failure modes — is what this project commits to instead.

**4. Real payment-risk teams evaluate the way Shivam's house discipline already prescribes.** Stripe Radar frames everything as a **precision/recall tradeoff at a chosen threshold**: per Stripe's engineering post "How we built it: Stripe Radar," fraud is "on the order of 1 out of every 1,000 payments," Radar "incorrectly blocks just 0.1%" of billions of legitimate payments, and decisions are made "in less than 100 milliseconds"; stripe.com/radar states Radar is "trained on 70 trillion data points" and "reduces fraud by 32% on average." Corgi Labs (built on Radar) evaluates over **rolling out-of-time windows** and reports dollar revenue impact (one client cited "a 22% increase in payments accepted, an 18% reduction in realized fraud, and more than $2 million in additional revenue"). That is the evaluation vocabulary this project adopts: an explicit operating point, cost expressed in currency, and out-of-time windows.

**5. A self-built synthetic generator is the right call for a "held-out test set" story.** Public options each have gaps: IEEE-CIS (590,540 txns, 3.5% fraud, `card1` entity) is rich but not card-testing-specific and Kaggle-competition-gated; PaySim (6.3M mobile-money txns, 0.13% fraud) is transfer/cash-out oriented; Sparkov (1.85M synthetic CNP txns) is generic CNP; the Kaggle `creditcardfraud` set is anonymized PCA with no BIN/IP/device fields. Recent research ("Synthetic Tabular Generators Fail to Preserve Behavioral Fraud Patterns," arXiv 2604.13125) shows off-the-shelf synthetic generators **destroy the velocity and multi-account signals** that card-testing detection depends on. So a **purpose-built, documented, deterministic-seed synthetic generator** that injects labeled card-testing bursts (same BIN, rotating card suffixes, shared IP/device, low-value probes, elevated declines) over a realistic benign baseline is both more defensible and more honest — and it is not "offense-capable" because scenarios are labeled test fixtures with non-actionable descriptions, not working attack tooling.

**6. The Rust-core/PyO3 architecture is feasible, and is built clean-room.** Current tooling is stable: PyO3 v0.28/0.29 (supports CPython 3.7-3.14, plus free-threaded 3.13t/3.14t) and maturin v1.13.x; the `abi3-py310` feature yields one forward-compatible wheel (Python 3.10+); the `numpy` crate provides zero-copy `PyReadonlyArray` for batch scoring; benchmark with `maturin develop --release` (debug builds are 10-20× slower). The detector needs only three primitives — per-entity ring buffers, EWMA baselines, sliding-window Welford variance — each of which is a small, well-understood piece of scalar Rust. **Nothing is reused from a prior library; there is no existing codebase behind this**, and no document in the repository may imply otherwise. Three things are deliberately excluded: SIMD (the per-entity update is scatter-heavy, the known failure case for vectorization, and the defensible claim here is bounded-memory p99 latency, which needs no SIMD), custom hash maps or allocators (`std::collections::HashMap` until profiling says otherwise, which at these volumes it will not), and union-find ring linkage (see A).

**7. The LLM adds honest value only in bounded, auditable triage.** The deterministic detector must make the flag decision; the LLM's job is to **explain** a flagged spike to an analyst (assemble the structured evidence: which BIN, decline rate, velocity vs baseline, window, rupee exposure) and optionally draft an analyst-facing rationale. Keep it behind a provider abstraction with cassette record/replay so tests are deterministic, and never let it change a label. (An evidence-bundle assembler mapped to Razorpay's dispute evidence schema is a credible *adjacent* feature, but it overlaps the Dispute Responder Agent, so it belongs only as an optional demo, not the core.)

## Details

### A. Recommended sub-direction: Fraud-spike detector (card-testing / velocity anomaly)

**Why this direction.** The stated bar is "a working detector, verifier or auto-responder for one class of loss, with measured precision and recall on a held-out test set… Honest metrics including false-positive cost. Strictly defense-only." A streaming card-testing spike detector satisfies every clause: it is a **detector** for **one crisp loss class** (card-testing/velocity fraud and the auth-fee + chargeback losses it causes); it produces **precision/recall on a temporally held-out test set**; its **false-positive cost is naturally expressible in rupees** (blocked good transactions + manual-review minutes); and it is inherently **defense-only** (it consumes an event stream and emits flags — it generates no attacks). It is also the direction whose core primitives are smallest — three statistical estimators over ring buffers, no model training, no graph layer — which is decisive in a 12-day solo window.

**Why the other directions were not chosen:**
- **Return-risk scorer** — directly duplicates Thirdwatch/Magic Checkout and the new RTO Shield/RTO Insights agents. Judges will see a reimplementation of a shipped product, and good RTO labels are hard to synthesize credibly.
- **Chargeback evidence responder** — duplicates the flagship Dispute Responder Agent launched at FTX'26, and the space is crowded (Corgi Labs, DisputeNinja/YC S23, Chargeflow). Rebuilding what Razorpay demoed six months earlier invites unflattering comparison.
- **Abuse-ring sentinel** — the most technically alluring option, but mule-ring detection is exactly what RBI's MuleHunter.AI and DPIP now cover, and unsupervised ring detection makes a clean "precision/recall on a held-out set" story much harder in 12 days. **Cut entirely, not deferred.** Carrying it as a stretch goal is how a 12-day build becomes a 16-day build; the distributed-testing scenario already exercises the multi-entity signal that a ring layer would have demonstrated.

### B. Project title options (Sanskrit/Vedic convention)

An independent availability check could not open every registry page, so **Shivam should confirm each name by loading `https://pypi.org/project/<name>/` and `https://crates.io/crates/<name>` before publishing.** With that caveat:

1. **Spandan** (स्पंदन, "pulse/throb/vibration") — *Recommended.* Thematically perfect: the detector watches the transaction "pulse" per BIN/IP/merchant and flags abnormal throbs. No major fintech/PyPI/crates collision surfaced (only an unrelated healthcare NGO and common personal-name usage).
2. **Lakshana** (लक्षण, "sign/symptom/characteristic") — cleanest availability of the set; frames the tool as reading the *symptoms* of an attack. Strong backup.
3. **Taranga** (तरंग, "wave/surge") — evokes the attack surge; verify availability.
4. **Spanda** (स्पन्द, "throb/pulse") — shorter variant of Spandan; verify availability.
5. **Vedha** (वेध, "piercing/penetration/detection") — connotes seeing through disguise.

**Avoid:** *Vega* (taken on both PyPI and crates.io, and a loaded options-trading term), *Anveshak* (collides with Bajaj Allianz's "Anveshak" insurance-fraud program and an OSINT security tool), and *Nirikshak* (collides with police "AI Nirikshak" surveillance and an AMNEX tracking product, plus a Dart package).

**Recommended pick:** **Spandan** — tagline: *"Spandan reads the pulse of your payment stream and catches card-testing storms before the chargebacks land."*

**Project Objectives (form answer, ready to paste):** "Spandan is a defense-only, streaming fraud-spike detector for card-testing and velocity abuse — the low-value 'probe' bursts (same BIN, rotating cards, shared IP/device) that spike auth-fee costs and downstream chargebacks for Indian merchants. A deterministic Rust core computes per-entity velocity and EWMA/Welford baselines in real time and flags anomalous spikes; a bounded, auditable LLM layer explains each flag to an analyst. It is evaluated on a temporally held-out synthetic test set with precision, recall, PR-AUC, calibration, and a false-positive cost model expressed in rupees."

### C. Architecture

**Rust core — crate `spandan-core`:**
- **Modules:** `ingest` (typed transaction event; fixed schema), `state` (per-entity store: per-BIN, per-IP, per-device, per-merchant counters), `velocity` (fixed-size ring buffers for sliding-window counts and decline ratios), `baseline` (EWMA + sliding-window Welford mean/variance per entity), `score` (z-score / robust deviation → spike score). Five modules, no sixth.
- **Data structures:** fixed-capacity ring buffers per entity to bound memory; `std::collections::HashMap` keyed by entity id. Struct-of-arrays layout only where it falls out naturally from the batch API — not built for its own sake.
- **APIs:** `score_batch(events: PyReadonlyArray) -> ndarray` (zero-copy NumPy via the `numpy` crate) for the eval harness; `Detector::update(event) -> Option<Flag>` streaming API for the live demo; deterministic given seed and config.
- **Bindings:** PyO3 v0.28/0.29 with `abi3-py310`, packaged by maturin v1.13.x; `maturin develop --release` for honest benchmarks.

**Python layer:**
- **`gen/`** — synthetic data generator: a documented benign baseline (per-merchant diurnal volume, realistic amount/BIN/IP/device distributions) plus labeled injected scenarios: *card-testing burst* (one BIN, many card suffixes, low amounts, high decline rate, one IP/device), *distributed testing* (rotating IPs), *slow-and-low* (sub-threshold micro-probes), and *benign spike* (a flash-sale volume surge that must **not** be flagged — the key false-positive test). Deterministic seeds; scenario descriptions non-actionable.
- **`eval/`** — temporal split (train on earlier window, test on strictly later window; never random); metrics: precision, recall, PR-AUC, calibration curve; **cost model in rupees** (blocked-good-txn value + manual-review cost per alert + saved chargeback/auth-fee exposure); ablations (drop EWMA, drop Welford, drop per-IP features); explicit failure-modes section.
- **`llm/`** — provider abstraction + cassette record/replay; single bounded task: given a structured flag, produce an analyst-facing explanation. Never mutates labels.
- **Cost-model anchors (documented, cited):** Razorpay-context chargeback/dispute fees ~₹500 per case (Razorpay blog ranges cited ₹200-750, and ₹500-2,000 for higher-risk categories; global benchmarks ₹1,500-8,000), plus Razorpay's own note that 20-40% of chargebacks stem from operational failures, not fraud — used to justify a conservative false-positive penalty and to argue that over-blocking is expensive.

**Optional Razorpay integration (cheap signal, not core):** a webhook listener stub for `payment.dispute.created` and a mapper into Razorpay's dispute evidence schema (`shipping_proof`, `billing_proof`, `access_activity_log`, etc. via the Documents API with `purpose=dispute_evidence`) [Razorpay](https://razorpay.com/docs/api/disputes/contest/) — demo only, clearly secondary.

**Demo surface:** a CLI (`spandan replay <stream>`) that streams the test set and prints flags + rupee exposure, plus a one-page FastAPI view showing the live pulse and flagged spikes. No heavy frontend.

### D. Claude Code build prompt (phase-gated, paste-ready)

> **Project: Spandan — streaming card-testing fraud-spike detector (Rust core + PyO3 + Python eval/LLM). Defense-only.**
>
> You are building this in strict phases. **After each phase, STOP and wait for my explicit approval before continuing.** Obey `agents.md` house rules at all times.
>
> **Phase 0 — Scaffold & house rules.** Create the repo: `spandan-core/` (Rust crate, PyO3 via maturin, `abi3-py310`, PyO3 0.28+/maturin 1.13+), `python/spandan/` (gen, eval, llm), `tests/`, `Makefile`, `README.md`, `BUILD_LOG.md`, `agents.md`. `agents.md` must encode: (1) **never delete files — use git to revert**; (2) **evidence over claims — every "done" is backed by a pasted command output**; (3) **no agent self-reports of success**; (4) **all LLM calls go through a provider abstraction with cassette record/replay**; (5) **temporal train/test split is mandatory; random splits are forbidden**; (6) **strictly defense-only — no attack/fraud-generation tooling; synthetic scenarios are labeled test fixtures with non-actionable descriptions only**. *Acceptance:* repo builds empty, `make` targets stubbed, `agents.md` committed. STOP.
>
> **Phase 1 — Synthetic data generator.** Implement `python/spandan/gen/` producing a benign baseline + labeled scenarios (card-testing burst, distributed testing, slow-and-low probes, and a benign flash-sale spike that must not be flagged). Deterministic seeds. Emit a strictly time-ordered event log and a held-out **temporal** test split (train = earlier window, test = later window). *Acceptance:* `make data` produces train/test files; a printed summary shows class balance, time ranges, and that all test timestamps are later than train. STOP.
>
> **Phase 2 — Rust core.** Implement `ingest`, `state` (per-entity store), `velocity` (ring buffers), `baseline` (EWMA + sliding-window Welford), `score`. Scalar only — no SIMD, no custom hash map. Unit tests for each; **property tests** (e.g., Welford variance matches a naïve two-pass computation within tolerance; ring buffer never exceeds capacity). *Acceptance:* `cargo test` green with output pasted; property tests included. STOP.
>
> **Phase 3 — PyO3 bindings & benchmarks.** Expose `score_batch` (zero-copy NumPy via the `numpy` crate) and streaming `Detector::update`. Build with `maturin develop --release`. Benchmark Rust vs a pure-Python/NumPy baseline on the same data; **report honest throughput and latency numbers, including cases where NumPy is competitive.** *Acceptance:* `make bench` prints a table (events/sec, p50/p99 latency, Rust vs NumPy) with the raw command output. STOP.
>
> **Phase 4 — Evaluation harness.** Compute precision, recall, PR-AUC, and a calibration curve on the **temporal** test set. Implement the **rupee false-positive cost model** (blocked-good-txn value + manual-review cost per alert vs saved auth-fee/chargeback exposure). Add **ablations** (remove EWMA / Welford / per-IP features) and a written **failure-modes** section (what it misses, what it over-flags). *Acceptance:* `make eval` reproducibly prints the metrics table, the PR curve artifact, the cost-in-rupees summary, the ablation table, and the failure-modes notes. STOP.
>
> **Phase 5 — LLM explanation layer.** In `python/spandan/llm/`, add a provider abstraction + cassette record/replay. One bounded task: given a structured flag, produce an analyst-facing explanation (BIN, decline rate, velocity vs baseline, window, rupee exposure). It must **never change a label**. *Acceptance:* tests run fully offline from cassettes; a sample explanation is shown. STOP.
>
> **Phase 6 — Packaging & polish.** Finalize `README.md` (problem, approach, metrics, how-to-run), an architecture diagram, `BUILD_LOG.md` (what broke and how it was fixed), a reproducible `make eval`, and a pitch-video checklist. *Acceptance:* a fresh clone runs `make eval` end-to-end; all artifacts present. STOP.
>
> **Stretch (only if ahead of schedule, clearly marked):** a Razorpay `payment.dispute.created` webhook stub + evidence-schema mapper, demo only. Nothing else. Do not add a ring/clustering layer, SIMD, or a custom hash map under any circumstances — these are cut by decision, not by schedule.

### E. 12-day schedule (Aug 24 – Sep 5; submit by Sep 3)

- **Aug 24-25 (Phase 0-1):** scaffold, `agents.md`, synthetic generator + temporal split.
- **Aug 26-29 (Phase 2):** Rust core + unit/property tests, written clean-room.
- **Aug 30 (Phase 3):** PyO3 bindings + honest benchmarks.
- **Aug 31 – Sep 1 (Phase 4):** eval harness, rupee cost model, ablations, failure modes.
- **Sep 2 (Phase 5):** LLM explanation layer with cassettes.
- **Sep 3 (Phase 6 + submit):** README, diagram, BUILD_LOG, record 5-min video, submit with buffer.
- **Sep 4-5:** buffer for overruns / stretch goal / re-record.

**Top risks & mitigations:**
1. *Synthetic data looks unrealistic to a risk panel* → document every assumption, calibrate distributions against published card-testing signatures (BIN-concentrated low-value probes, elevated declines, shared IP/device), and include the benign-spike negative case to prove low false positives.
2. *Clean-room Rust core takes longer than estimated* → the three estimators are individually small and independently testable; if Phase 2 slips past Aug 29, drop the per-device entity axis and ship with BIN/IP/merchant only, which costs one scenario's recall and nothing else.
3. *Rust core doesn't beat NumPy on small batches* → report honestly; emphasize streaming p99 latency and bounded memory, which is where Rust wins.
4. *Scope creep into ring detection, SIMD, or custom data structures* → all three are cut in `agents.md` and in the phase prompts, not merely deprioritized.
5. *LLM nondeterminism breaks tests* → cassette record/replay from Phase 5, enforced by `agents.md`.

### F. 5-minute pitch outline & "Build Challenges" field

**Pitch (5 min):** (1) *Problem* (30s) — card-testing/velocity attacks quietly burn Indian merchants via auth fees + chargebacks; Razorpay ships RTO and dispute tools but not this. (2) *Approach* (60s) — deterministic Rust streaming detector (EWMA/Welford/SoA) + bounded LLM triage; defense-only. (3) *Live demo* (90s) — `spandan replay` streams the test set; watch the benign flash-sale pass and a card-testing burst light up with rupee exposure; show the analyst explanation. (4) *Metrics slide* (60s) — precision/recall/PR-AUC on temporal split, calibration, **false-positive cost in rupees**, Rust-vs-NumPy latency/throughput. (5) *Failure handled gracefully* (30s) — show one class it misses and how the cost model still keeps net savings positive. (6) *What broke* (30s) — one concrete bug from BUILD_LOG and the fix.

**"Build Challenges & Technical Obstacles" (structure to fill):** (a) *Temporal leakage* — why random splits inflate scores and how the strict later-window split fixed it; (b) *PyO3 zero-copy* — the NumPy interop / abi3 gotcha and resolution; (c) *Welford correctness* — the property test that caught a variance bug; (d) *LLM determinism* — cassette record/replay so eval is reproducible; (e) *Honest benchmarking* — where NumPy was competitive and why streaming latency still favors Rust. Each backed by a BUILD_LOG entry and a command output.

## Recommendations
1. **Commit to the fraud-spike detector now** and name it **Spandan** — after confirming PyPI/crates availability by loading the registry pages. This is the highest-leverage, least-duplicative choice given Razorpay's shipped products and Shivam's assets.
2. **Budget four days for the clean-room Rust core (Aug 26-29)** — EWMA, sliding-window Welford, and ring buffers are each small, but they are being written from scratch, and the property tests are what make them trustworthy.
3. **Make the rupee false-positive cost model the centerpiece** — it is the single thing that most separates this from student XGBoost dashboards and speaks directly to the panel's "false-positive cost" bar.
4. **Keep the LLM strictly explanatory and cassette-tested** — never let it decide a flag; this preserves the "deterministic detector" integrity the judges will probe.
5. **Treat the Razorpay webhook mapper as the only stretch goal** — ring detection, SIMD, and custom data structures are cut, not deferred. Protect the 12-day core.
- **Signals that would change the plan:** if the Rust core is not passing property tests by Aug 29, narrow the entity axes rather than switching tracks; if synthetic data cannot be made convincing by Aug 30, supplement the negative-class realism using IEEE-CIS's benign distribution as a sanity anchor (do not adopt its labels).

## Caveats
- The Track 2 official text, buildathon terms (₹75,000/month, 6/12 months, Bangalore, applications close 5 September 2026), and product facts are drawn from Razorpay's own pages (razorpay.com/buildathon, newsroom, docs, blog) and reputable coverage, but **program details on third-party job aggregators can drift** — Shivam should re-confirm requirements on razorpay.com/buildathon before submitting.
- **This design assumes no pre-existing code.** An earlier draft of this report proposed reusing primitives from a library called Prahari; that library has not been built, so every Rust component here is scoped as clean-room work and the estimates reflect that.
- **Project-name availability was not fully verifiable** across PyPI and crates.io (only *Vega* was confirmed taken); confirm the chosen name before publishing the repo.
- Fraud statistics vary by source and reporting window (RBI annual figures, CPFIR/Lok Sabha disclosures, NPCI/PIB releases, and private surveillance vendors differ); treat them as directional and cite the specific source used in the pitch. The ₹485 crore / 6.32 lakh figure is H1 FY2024-25 (Apr–Sep 2024), not a full year.
- MuleHunter.AI accuracy figures (RBIH's "85%+"; Canara Bank's "95%") come from RBI/vendor statements, not an independent audit; RBI has declined RTI requests for aggregate mule-detection outcomes.