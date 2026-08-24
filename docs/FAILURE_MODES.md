# Failure modes

What Spandan misses, what it over-flags, and where its numbers cannot be trusted.
Every claim is backed by a measurement from `make eval` or `spandan replay`, not
by inspection of the design.

State: end of Phase 2, after the review addendum. Stream is 100 days, 1.61M
events, 240 episodes (20 per scenario per split), three seeds.
Phase 6 refreshes these against the final detector.

---

## 0. What the multi-seed check settled, and what it did not

Nothing in this document should be read from a single stream. Every headline
below is a median across three independently generated streams, with the range.

| Metric | min | median | max | spread |
|---|---|---|---|---|
| precision | 0.4003 | **0.4564** | 0.5892 | 0.189 |
| recall | 0.8234 | **0.8573** | 0.8696 | **0.046** |
| PR-AUC | 0.6192 | 0.6615 | 0.7928 | 0.174 |
| net rupees | ₹226,409 | ₹395,017 | ₹408,038 | ₹181,630 |
| alerts/day | 3.9 | 6.8 | 27.6 | **23.7** |
| headroom % | −3.016 | −2.820 | −2.416 | 0.599 |

**Recall is now stable and precision is not.** Recall's spread fell from 0.386 to
**0.046** when the test window went from 6 attack episodes to 60 — that was the
underpowered-evaluation problem, and it is fixed. Precision still swings 0.40–0.59
because it is dominated by false positives on one control (§2.1), whose volume
varies with the stream.

**Alerts/day ranges 3.9 to 27.6.** That is the least stable number in the project
and the one an operations team would care about most. A detector that might
generate four alerts a day or might generate twenty-eight is not yet a product.

---

## 1. The strongest result: detection speed

| Scenario | episodes caught | median events before first flag | p90 | median rupees exposed |
|---|---|---|---|---|
| `burst` | **20/20** | 0 | 13 | ₹0 |
| `rotating` | **20/20** | 0 | 6 | ₹0 |
| `slow_low` | **20/20** | 1 | 7 | ₹11 |

Every episode of every attack scenario is caught, and the median episode is caught
on its **first or second event**, before any material value has moved.

This is the number closest to the product. A classifier that eventually labels a
burst correctly and a detector that catches it on attempt one are both "recall
high" and are not the same thing to a merchant.

**`slow_low` is no longer the weak scenario.** At the Phase 2 threshold it caught
1 of 2 episodes at 1.7% event recall; it now catches 20/20 with a median of one
event. That change came from the operating point moving (threshold 25.90 → 21.15),
not from any change to the detector — which is itself a warning about how much the
Phase 2 per-scenario conclusions depended on where the line fell.

Event-level recall is 0.857. Episode-level detection is 60/60. Both are reported;
neither replaces the other.

---

## 2. What it over-flags

### 2.1 The single-merchant issuer outage. This is the headline failure.

| Control | axis attacked | events | flagged | rate | blocked-good cost |
|---|---|---|---|---|---|
| `flash_sale` | volume | 17,787 | 38 | 0.0021 | ₹644 |
| `issuer_outage` | decline ratio | 18,057 | 2,066 | 0.1144 | **₹2.30L** |
| **`outage_single_merchant`** | decline ratio, no crutches | 18,169 | **10,759** | **0.5922** | ₹13,121 |
| `benign` | — | 740,349 | 1,084 | 0.0015 | ₹46,940 |

**59% of a legitimate single-merchant issuer outage is flagged as card testing.**

**And notice what the rupee column does with that.** The worst failure mode by
count — 10,759 false positives — costs ₹13,121, while a control flagged five times
less often costs ₹2.30L. Two reasons, both of which are limitations of the cost
model rather than mitigations:

1. `outage_single_merchant` carries low-value baskets by construction, so the
   contribution margin lost per blocked transaction is small.
2. Most of its traffic was declining anyway, so blocking it costs no margin at
   all (§6).

A rupee model that scores 10,759 wrongly-blocked legitimate transactions at ₹13k
is telling you something about the model, not about the detector. **Do not read
the low cost as evidence that this failure is unimportant.** A merchant whose
customers are being blocked during an issuer outage does not experience it as a
₹13,121 event, and none of the reputational cost is represented anywhere in
`costs.toml`.

The headroom is negative on every seed (−2.4 to −3.0 times the threshold): the
highest-scoring clean event scores **80.79** against a threshold of **21.15**. The
control is not being rejected at all. It is being scored like an attack, and the
only thing separating the two populations is where the line happens to fall.

This was built deliberately, on review instruction, after Phase 2 measured *which*
separator was doing the work on the multi-merchant outage. The answer was that the
detector was rejecting it on **merchant span and amount** — not on the retry
structure the control was designed around. `outage_single_merchant` removes both
crutches: one merchant, and amounts in the same low band as a probe burst.

What is left is retry structure, and **the detector cannot see it** (§2.2).

Consequences, stated plainly:

- Precision at the observed prevalence is 0.456 median. At the stated realistic
  0.15% prevalence it falls to **0.069**.
- A merchant running this detector during an issuer outage would have most of
  their legitimate declining traffic flagged, at the worst possible moment.
- This is a real, common, well-understood payments event. It is the first
  false-positive case a risk panel raises, and the honest answer today is that
  the detector fails it.

### 2.2 The retry separator is invisible at the window size chosen

The design intent was that retries distinguish an outage from a probe run: an
outage re-attempts the same card, a probe does not revisit a dead card. Measured
over a whole episode that holds — roughly 4.7 attempts per card against a burst's
1.0.

Inside the detector's **5-minute** window it does not hold. An outage's retries are
spread across a 60-minute episode, so any single window sees mostly distinct cards.
Working it through: ~70 events drawn from ~290 cards in a 5-minute window yields
about 63 distinct cards, or 1.13 attempts per card — while a burst packing 240
events into 12 minutes yields about 1.22 in-window attempts per card. **The
damping term is not merely weak here; at this window size it points the wrong
way.**

The `repetition` term is therefore close to dead weight, and possibly harmful.
Fixing it needs a second, much longer window on the BIN axis — a design change,
not a parameter change.

### 2.3 Cold start

| | flagged | truly card testing | false positives |
|---|---|---|---|
| warmed on prior events | 230 | 230 | 0 |
| `--cold-start` | 29 | 24 | 5 (all `issuer_outage`) |

With empty baselines a BIN's baseline window count sits near 1.0 with almost no
variance, so ordinary traffic scores 17–21 standard deviations above it. A
detector deployed against a new merchant, or restarted without persisted state,
over-flags for its first window of operation.

Found because the replay demo disagreed with `make eval` (BUILD_LOG). Demonstrable
on purpose via `spandan replay --cold-start`. **Mitigation not built:** persist
per-entity baselines across restarts.

### 2.4 Over-flagging the evaluation still cannot see

- **Gateway incidents** — decline rates up a few points for a few minutes. Not
  generated, so not measured.
- **Shared-IP benign traffic** — corporate NAT, carrier-grade NAT, household
  devices. Cards, IPs and devices are drawn independently, so no benign entity
  concentration exists and the per-IP term is never tested against a legitimate
  reason for one.
- **Retry storms after a network blip.**

---

## 3. The ablations: a Phase 2 claim, retracted

**Phase 2 reported that dropping the per-entity EWMA baseline improved net
position by ₹17,159 and concluded the EWMA "is not carrying the detection
signal". That result was measured on one stream. It does not survive.**

Median across 3 streams, with [min, max]:

| variant | precision | recall | net rupees |
|---|---|---|---|
| full | 0.456 [0.40, 0.59] | 0.857 [0.82, 0.87] | 395,017 [226,409, 408,038] |
| drop-EWMA | 0.356 [0.35, 0.37] | 0.844 [0.77, 0.85] | 364,542 [335,852, 407,998] |
| drop-per-IP | 0.420 [0.41, 0.73] | 0.873 [0.78, 0.92] | 355,738 [264,975, 411,118] |

Paired per seed, which is the only comparison that controls for the stream:

| ablation | net delta vs full (median) | range | beats full on | verdict |
|---|---|---|---|---|
| drop-EWMA | ₹12,981 | [−₹43,496, +₹109,443] | 2 of 3 seeds | **not consistent** |
| drop-per-IP | ₹16,101 | [−₹52,300, +₹38,566] | 2 of 3 seeds | **not consistent** |

Both deltas change sign across streams. Neither ablation shows a consistent
effect, and the per-seed range is several times the median gap.

**The honest conclusion is a null result, and it is stated as one:**

- The Phase 2 claim that EWMA is not carrying the signal was **seed noise**, and
  is withdrawn.
- The opposite claim — that EWMA is vindicated — is **equally unsupported**. Its
  median net is higher than both ablations, but it loses on one seed out of three.
- A three-seed test cannot resolve a gap this small relative to its variance.
  Resolving it needs either many more seeds or a variance-reduction design
  (common random numbers across variants), and that is a measurement question,
  not an architecture question.

What can be said with the data in hand: **drop-EWMA costs precision consistently**
(0.356 median vs 0.456, and its range 0.35–0.37 does not overlap full's 0.40–0.59).
The net-rupee effect is ambiguous; the precision effect is not.

### 3.1 What the README should argue, and why

Deferred until this table existed, per review. Now that it does:

The write-up should state that **the cost model as parameterised is close to
indifferent between the full detector and the ablations, and that this is a
statement about the cost model rather than about the components.** Specifically:

- The rupee model treats review cost as **linear** in alert count. At ₹40 an
  alert, 27 alerts/day and 4 alerts/day differ by about ₹900 a day, which is
  noise next to the chargeback exposure term.
- Linear review cost understates **alert fatigue**. A team receiving 28 alerts a
  day does not review them 7× as carefully as a team receiving 4; response
  quality degrades, and past some volume alerts stop being read. That effect is
  real, well documented in security operations, and **not modelled here**.
- So the two configurations are not the same product even where the model says
  they are worth the same.

**Recommendation:** argue for the full configuration on the basis of precision
(0.456 vs 0.356, non-overlapping ranges) and alert volume, state explicitly that
the rupee model does not separate them, and name linear review cost as the
model's known limitation. Do not present any component as vindicated by the
ablation table — it does not vindicate anything, and claiming otherwise is the
exact failure this project exists to avoid.

---

## 4. Where the numbers are optimistic by construction

From `gen/ASSUMPTIONS.md` §2:

- **Positive rate ~1.33%**, far above real merchant card-testing rates. Precision
  is reported at both the observed rate and a stated 0.15%; at the target it falls
  from 0.456 to 0.069.
- **Perfect labels**, no label noise, no late-arriving chargebacks.
- **One diurnal shape for every merchant**, so the baseline is more predictable
  than reality.
- **Attacks are single-merchant.** Real campaigns sweep many merchants at once.
  Note the asymmetry this creates with the outage controls, and that
  `outage_single_merchant` was added precisely to stop the detector leaning on it.

---

## 5. What was not measured, and why

- **Only two ablations.** Drop-Welford and drop-per-device were cut on 2026-08-24
  to fund the outage controls. On the evidence in §2.2, the `repetition` term is
  now the one most worth ablating.
- **No latency or throughput numbers.** Phase 4.
- **Cold-start cost is counted, not costed** — the replay path does not run the
  cost model.
- **Variance reduction was not attempted.** §3's null result is a consequence.

---

## 6. Cost-model sensitivities

Gross ₹2.82L on the headline seed: avoided chargeback exposure ₹5.59L, saved
authorization fees ₹13,962, blocked good transactions −₹2.91L.

Two things worth noticing:

- **11,800 of the 13,947 blocked clean transactions were going to decline
  anyway**, and so cost the merchant no margin. That is the outage controls
  showing up in the cost model exactly as intended — flagging a declining
  transaction is cheap in rupees even when it is wrong. It is *not* cheap in
  trust, and the cost model does not capture that.
- The net position is dominated by the chargeback term, which is the product of
  two uncitable assumptions (a ₹500 dispute fee, an 0.8 chargeback rate on
  approved fraud). Halving the assumed rate roughly halves the headline saving.

**The break-even review cost is ₹204 per alert** on the headline seed — down from
₹5,478 in Phase 2, because alert volume rose from 6 to 1,379. That is the figure
to quote, because it is an output rather than an assumption: the detector pays for
itself as long as reviewing an alert costs under ₹204. At 27.6 alerts/day that is
a real operational constraint rather than a comfortable margin.

---

## 7. Recommendations arising from Phase 2

Ordered by how much they change the credibility of the submission:

1. **Give the detector a long-horizon window** on the BIN axis so retry structure
   is visible at the scale it actually occurs (§2.2). This is the single change
   most likely to fix §2.1, which is the worst result in the project.
2. **Model alert fatigue, or stop reporting net rupees as the deciding metric**
   (§3.1). Linear review cost is why the cost model cannot separate the variants.
3. **Reduce variance before running ablations again** — common random numbers
   across variants, or more seeds. §3 is currently a null result for measurement
   reasons, not architectural ones.
4. **Persist baselines across restarts** to remove the cold-start failure (§2.3).
