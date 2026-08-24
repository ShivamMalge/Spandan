# Failure modes

What Spandan misses, what it over-flags, and where its numbers cannot be trusted.
Every claim here is backed by a measurement from `make eval` or `spandan replay`,
not by inspection of the design.

Draft as of the Phase 2 gate. Numbers are from the shipped stream
(`config_hash a4d5c839…`, seed 20260824) unless stated. Phase 6 refreshes them
against the final detector.

---

## 0. The headline finding: the numbers are not stable across streams

**This is the most important item in this document, and it outranks every metric
elsewhere in the project.**

Running the identical evaluation on three independently generated streams:

| Metric | min | median | max | spread |
|---|---|---|---|---|
| precision | 0.6707 | 0.8732 | 1.0000 | **0.3293** |
| recall | 0.1594 | 0.5191 | 0.5448 | **0.3855** |
| PR-AUC | 0.6977 | 0.7951 | 0.9239 | 0.2262 |
| net rupees | ₹1,294 | ₹30,856 | ₹32,631 | ₹31,337 |
| alerts/day | 1.5 | 2.5 | 3.8 | 2.3 |

Per seed:

| seed | threshold | PR-AUC | precision | recall |
|---|---|---|---|---|
| 20260824 | 25.90 | 0.9239 | 1.0000 | 0.5448 |
| 20260825 | 39.09 | 0.6977 | 0.6707 | 0.1594 |
| 20260826 | 26.02 | 0.7951 | 0.8732 | 0.5191 |

**Quoting "precision 1.00, recall 0.54" as the result would be reporting noise.**
The defensible statement is the median with the range attached.

There are two distinct causes and they need different fixes:

1. **The test window is underpowered.** It contains six attack episodes — two per
   scenario. Any per-scenario recall therefore rests on a sample of two, and
   episode-level variation dominates everything downstream. This is a flaw in the
   evaluation design, not a property of the detector, and it errs in both
   directions rather than flattering the result.
2. **Threshold selection chases a bumpy net curve.** Alert counts move in steps,
   so the net-versus-threshold curve is not smooth, and the selected threshold
   moved from 25.90 to 39.09 across streams. PR-AUC is threshold-free and still
   spreads by 0.23, so the underlying separability varies too — threshold
   selection compounds a real problem rather than being the whole of it.

Neither has been fixed, because fixing them changes the generator and the
selection rule, and both are gated decisions. The recommendation is in §7.

---

## 1. What it misses

### 1.1 Slow-and-low is the weakest scenario, as predicted

| Scenario | episodes caught | event recall | median events before first flag | median rupees exposed |
|---|---|---|---|---|
| `burst` | 2/2 | 0.6894 | 2 | ₹39 |
| `rotating` | 2/2 | 0.5326 | 4 | ₹121 |
| `slow_low` | **1/2** | **0.0169** | 5 | ₹62 |

At the cost-optimal threshold, `slow_low` catches one episode of two and 1.7% of
its events. At the more conservative threshold selected before the tie-break rule
was added, it caught **zero of two**.

This is the scenario the plan predicted would be hardest, and it is. The cause is
structural rather than incidental: the detector's velocity term measures events
per five-minute window against a per-entity baseline, and an episode spread over
six hours never produces an unusual five-minute window. It is not a tuning
failure; a detector with a five-minute window cannot see a six-hour signal.

**What would fix it:** a second, much longer window (hours rather than minutes) on
the BIN axis. That is a design change, not a parameter change, and it is not in
the Phase 2 scope.

### 1.2 Event recall understates operational performance, and both are reported

Event-level recall is 0.54. Episode-level detection is 5 of 6. These measure
different things and the difference is not a rounding artifact: an episode has to
be caught **once** to be acted on, so event recall penalises the detector for not
flagging every subsequent event of an episode it already caught. Neither number
replaces the other, and reporting only the flattering one would be dishonest in
either direction depending on the audience.

### 1.3 Time to detection is the number closest to the product

Median 2 events and ₹39 exposed for `burst`, 4 events and ₹121 for `rotating`.
Both are well inside the window where a merchant can act. This is the metric most
likely to matter in production and the one least represented in comparable
submissions.

---

## 2. What it over-flags

### 2.1 Cold start — the real one, and it was caught by accident

A detector with no per-entity history over-flags exactly the traffic that most
resembles an attack. Measured on the first 30,000 test events:

| | flagged | truly card testing | false positives |
|---|---|---|---|
| warmed on 152,035 prior events | 230 | 230 | **0** |
| `--cold-start` | 29 | 24 | **5** (all `issuer_outage`) |

With cold baselines, a BIN's baseline window count sits near 1.0 with almost no
variance, so an ordinary issuer-outage window scores 17–21 standard deviations
above it.

This surfaced because the replay demo disagreed with `make eval` — the demo was
starting cold while the eval warmed on the training window. The fix was to warm
the demo, but the underlying property is real: **a detector deployed against a new
merchant, or restarted without persisted state, will over-flag for its first
window of operation.** `spandan replay --cold-start` demonstrates it deliberately.
See `BUILD_LOG.md`.

**Mitigation not built:** persist per-entity baselines across restarts, and
suppress scoring for an entity until `baseline_min_samples` is reached on a
per-entity rather than global basis.

### 2.2 The negative controls hold, but with almost no headroom

At the selected threshold both controls produce **zero** false positives:

| Control | axis attacked | flagged | blocked-good cost |
|---|---|---|---|
| `flash_sale` | volume | 0 | ₹0 |
| `issuer_outage` | decline ratio | 0 | ₹0 |
| `benign` | — | 0 | ₹0 |

That `FP = 0` is not a result on its own. The headroom is:

- highest-scoring clean event: **25.06** (a `flash_sale` event)
- threshold: **25.90**
- headroom: **0.85, or 3.3% of the threshold**

A 3.3% margin is not a comfortable separation. Read alongside §0: on seed
20260825 that margin closed and precision fell to 0.67.

### 2.3 The retry separator does not work at the window scale chosen

The issuer-outage control was designed to be distinguishable from a burst partly
by card retries — 5.36 attempts per card against a burst's 1.58. **That separator
is largely invisible to the detector**, because the retries are spread across a
60-minute episode while the damping term measures repetition inside a 5-minute
window. Within any single window the outage's cards look almost all distinct.

The outage is rejected mainly by the merchant-span term and by amount, not by
repetition. The repetition term is close to dead weight at the current window
size, and a fair ablation would probably show it contributing little. It was not
one of the two ablations run (see §5).

### 2.4 Unmodelled over-flagging the evaluation cannot see

- **Gateway incidents** — the milder, more frequent cousin of a full issuer
  outage: decline rates up a few points for a few minutes. Not generated, so not
  measured. A detector tuned against large outages may over-flag small ones.
- **Shared-IP benign traffic** — corporate NAT, carrier-grade NAT, a household
  device. The generator draws cards, IPs and devices independently, so no benign
  entity concentration exists at all, and the per-IP term is never tested against
  a legitimate reason for it.
- **Retry storms after a network blip** — benign arrivals are Poisson, so the
  only benign burst the detector faces is the flash sale.

---

## 3. Where the ablations undercut the design

| variant | precision | recall | PR-AUC | net | alerts |
|---|---|---|---|---|---|
| full | 1.0000 | 0.5448 | 0.9239 | ₹32,631 | 6 |
| drop-EWMA | 0.9005 | 0.8635 | 0.9040 | **₹49,790** | 125 |
| drop-per-IP | 1.0000 | 0.4742 | 0.9286 | ₹28,846 | 4 |

**Removing the per-entity EWMA baseline makes the detector more profitable**, by
₹17,159 on this stream — it catches far more (recall 0.86 vs 0.54) at modest
precision cost. Reported as measured, per `agents.md` §6.

The honest reading: the per-entity baseline is not carrying the detection signal.
What it buys is **alert volume** — 6 alerts versus 125, which is 1.5/day versus
31/day. Under this cost model, where the assumed review cost is ₹40 and the
break-even is ₹5,478, that trade is not obviously worth ₹17,159. Under a cost
model with a realistic analyst capacity constraint it might be.

This is a genuine argument against part of the architecture, and it is exactly the
sort of thing a Rust core built around per-entity EWMA/Welford state should have
to answer. It does not invalidate the Rust work — bounded-memory per-entity state
is still what the streaming story rests on — but the claim "EWMA carries the
signal" is not supported and will not be made.

`drop-per-IP` costs 0.07 recall and slightly *improves* PR-AUC, which is
consistent with the per-IP term helping on `burst` and doing nothing for
`rotating` by construction.

---

## 4. Where the numbers are optimistic by construction

From `gen/ASSUMPTIONS.md`, the items that most affect these metrics:

- **Positive rate ~1.6%** in the test window, far above real merchant
  card-testing rates. Precision is reported at both the observed rate and a
  stated 0.15% target; at the target, precision is unchanged only because FP=0.
  On any stream where FP > 0, the reweighted figure is the one to quote.
- **Perfect labels.** No label noise, no late-arriving chargebacks.
- **One diurnal shape for every merchant**, making the baseline more predictable
  than reality.
- **Attacks are single-merchant, outages are multi-merchant.** Merchant span is
  therefore a stronger separator here than it would be against a real
  multi-merchant campaign, and the detector leans on it.

---

## 5. What was not measured, and why

- **Only two ablations.** Drop-Welford and drop-per-device were cut on 2026-08-24
  to fund the issuer-outage control. The repetition term (§2.3) is the one most
  worth ablating next, on the evidence above.
- **No latency or throughput numbers.** Phase 4.
- **Cold-start cost is measured but not costed** — the 5 false positives in §2.1
  are counted, not converted to rupees, because the replay path does not run the
  cost model.

---

## 6. Cost-model sensitivities

The net position is dominated by avoided chargeback exposure (₹32,014 of ₹32,871
gross). That term is the product of two assumptions — a ₹500 dispute fee and an
0.8 chargeback rate on approved fraud — neither of which is independently
citable. Halving the assumed chargeback rate roughly halves the headline saving.

The saved-authorization-fee term (₹856) is deliberately small so the headline does
not lean on the least defensible parameter.

The **break-even review cost of ₹5,478 per alert** is the figure to quote, because
it is an output. At 1.5 alerts/day, the detector pays for itself unless reviewing
an alert costs more than about ₹5,500 — which is a claim a payments panel can
check against their own analyst costs rather than having to accept ours.

---

## 7. Recommendations arising from Phase 2

Ordered by how much they change the credibility of the submission:

1. **Fix the underpowered test window** by generating more attack episodes per
   scenario. Six episodes cannot support per-scenario claims. This is a generator
   config change and a regeneration, roughly an hour, and it makes every number
   in the project more trustworthy. It is not "tuning to look better" — an
   underpowered evaluation errs in both directions.
2. **Add a long-horizon window** on the BIN axis so `slow_low` is detectable at
   all. Design change, not tuning.
3. **Re-examine the repetition term** at a window size where it can actually fire,
   or drop it and let merchant span and amount carry the outage rejection.
4. **Decide the EWMA question deliberately** (§3), and say in the README which
   trade was chosen and why, rather than letting the ablation table raise a
   question the write-up does not answer.
