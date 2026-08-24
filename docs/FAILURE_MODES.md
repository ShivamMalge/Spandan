# Failure modes

What Spandan misses, what it over-flags, and where its numbers cannot be trusted.
Every claim is backed by a measurement from `make eval` or `spandan replay`, not
by inspection of the design.

State: end of Phase 2, after the review addendum and the operating-point fix.
Stream is 100 days, 1.61M events, 240 episodes (20 per scenario per split), three
seeds. **The detector is frozen from this point through Phase 3** so that the Rust
port has a stable parity spec. Phase 6 refreshes these numbers.

---

## The headline

**Precision at a realistic merchant card-testing base rate (0.15%, assumed) is
0.0956 — roughly nine false alarms for every true catch.**

That is the number a payments panel will look for, so it is the number this
document and `make eval` both open with. Measured at the generator's own ~1.3%
positive rate precision is 0.4867, and that figure flatters the detector by about
an order of magnitude.

Recall is 0.788, and every one of the 60 attack episodes in the test window is
caught, at a median of 2 events and ₹65–95 exposed.

The gap between those two sentences is the honest summary of where this project
stands: it detects reliably and fast, and it costs too many false alarms to
deploy as-is against the one failure mode described in §2.1.

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

### 0.1 The operating point was being chosen by a cost model this document shows is wrong

Phase 2 selected the threshold by maximising net rupees. §6 then demonstrated that
the cost model prices 10,759 wrongly-blocked legitimate transactions at ₹13,121,
because that traffic carries low-value baskets and was mostly declining anyway.

Those two facts together are a problem, and the Phase 2 addendum did not follow
the implication: **an objective that treats false positives as nearly free will
buy recall with them, and it did.** The unconstrained criterion landed on
threshold 21.15, 27.6 alerts/day, and precision 0.0693 at a realistic base rate.

The fix does not touch the detector and does not require the rupee model to price
false positives correctly. The criterion is now **maximise net rupees subject to
an alerts/day cap**, with the cap set to what one analyst can plausibly work
through (10/day, an assumption stated in `costs.toml [operations]`).

| | unconstrained | constrained (≤10 alerts/day) |
|---|---|---|
| threshold | 21.15 | **23.05** |
| alerts/day | 27.6 | **6.2** |
| precision | 0.4003 | **0.4867** |
| precision @ 0.15% | 0.0693 | **0.0956** |
| recall | 0.8696 | 0.7884 |
| episodes caught | 60/60 | 60/60 |
| median TTD | 0 events | 2 events |
| break-even review cost | ₹204 | **₹985** |

The frontier, printed by `make eval` as a sensitivity table:

| alerts/day budget | threshold | alerts/day | precision | prec @ 0.15% | recall | episodes | median TTD | net |
|---|---|---|---|---|---|---|---|---|
| 2 | 28.77 | 2.0 | 0.718 | 0.2214 | 0.597 | 35/60 | 7 | ₹308,616 |
| 5 | 24.96 | 3.5 | 0.569 | 0.1281 | 0.706 | 51/60 | 5 | ₹324,386 |
| **10** | **23.05** | **6.2** | **0.487** | **0.0956** | **0.788** | **60/60** | **2** | **₹294,844** |
| 20 | 23.05 | 6.2 | 0.487 | 0.0956 | 0.788 | 60/60 | 2 | ₹294,844 |
| 50 | 21.15 | 27.6 | 0.400 | 0.0693 | 0.870 | 60/60 | 0 | ₹226,409 |

Two things this table says that a single operating point could not:

- **Net rupees is nearly flat across the whole range** (₹226k–₹324k) while
  precision at a realistic base rate varies by 3×. The cost model genuinely
  cannot see the difference that matters most, which is the §6 limitation
  showing up in the place it does the most damage.
- **The budget is not a free win.** Tightening from 10 to 2 triples precision and
  costs 25 of 60 episodes.

This is a **sensitivity analysis, not a menu.** The headline budget was fixed in
`costs.toml` before the test window was read. Picking a row from this table after
seeing these numbers would be selecting on the test set.

---

## 1. Detection speed, at the constrained operating point

| Scenario | episodes caught | median events before first flag | p90 | median rupees exposed |
|---|---|---|---|---|
| `burst` | **20/20** | 2 | 36 | ₹83 |
| `rotating` | **20/20** | 4 | 23 | ₹95 |
| `slow_low` | **20/20** | 2 | 7 | ₹65 |

Every episode of every attack scenario is caught, at a median of 2–4 events and
under ₹100 of exposure.

**These are not the numbers Phase 2's addendum reported, and the earlier ones
should be disregarded.** That run showed a median of 0 events and ₹0 exposed,
which is not a triumph — it is what a threshold of 21.15 does. Catching every
episode on its first event is trivially achievable by flagging almost everything,
and the figure was an artifact of the operating point rather than a property of
the detector. Measured at the constrained operating point the numbers are worse
and they are real.

This remains the number closest to the product, but only when it costs something.
A classifier that eventually labels a burst correctly and a detector that catches
it on attempt two are both "recall high" and are not the same thing to a
merchant.

**`slow_low` is no longer the weak scenario**, but read that carefully. It caught
1 of 2 episodes at 1.7% event recall in Phase 2 and now catches 20/20. None of
that came from a change to the detector — the detector is unchanged. It came from
the operating point moving and from the test window growing from 2 episodes to 20.
Its event-level recall is still the lowest of the three at 0.267.

Event-level recall is 0.788 overall. Episode-level detection is 60/60.
**Alert-level precision is 0.564** — of the 312 alerts a human would open, 176 are
genuinely card testing. That is the number an analyst actually experiences, and it
is neither the event-level precision (0.487) nor the base-rate-adjusted figure
(0.096). All three are reported because they answer different questions.

---

## 2. What it over-flags

### 2.1 The single-merchant issuer outage. This is the headline failure.

At the constrained operating point (threshold 23.05):

| Control | axis attacked | events | flagged | rate | blocked-good cost |
|---|---|---|---|---|---|
| `flash_sale` | volume | 17,787 | 4 | 0.0002 | ₹0 |
| `issuer_outage` | decline ratio | 18,057 | 1,573 | 0.0871 | **₹1.79L** |
| **`outage_single_merchant`** | decline ratio, no crutches | 18,169 | **7,240** | **0.3985** | ₹8,595 |
| `benign` | — | 740,349 | 84 | 0.0001 | ₹29,139 |

The `flash_sale` control is now essentially clean (4 events of 17,787). The volume
axis is handled. The decline-ratio axis is not.

**At the constrained operating point, 39.9% of a legitimate single-merchant issuer
outage is flagged as card testing** (7,240 of 18,169 events; it was 59.2% at the
unconstrained point).

**The negative headroom is the finding, not a failure to report.** The
highest-scoring clean event scores 80.79 against a threshold of 23.05 — headroom
−250% — on every seed. What that identifies precisely: the detector has a blind
spot on *legitimate traffic whose declines are concentrated on one BIN at one
merchant*. It cannot separate that from card testing, because the one feature that
would — the same card retried — is invisible at the window size it uses (§2.2).

The control was built to find exactly this, and it found it. A negative-headroom
result from a purpose-built control is the control working, and it is a sharper
statement about the detector than any precision figure: not "it makes mistakes at
this rate" but "there is a specific, common, nameable class of legitimate traffic
it cannot see the difference from.".

**And notice what the rupee column does with that.** The worst failure mode by
count — 7,240 false positives — costs ₹8,595, while a control flagged five times
less often costs ₹1.79L. Two reasons, both of which are limitations of the cost
model rather than mitigations:

1. `outage_single_merchant` carries low-value baskets by construction, so the
   contribution margin lost per blocked transaction is small.
2. Most of its traffic was declining anyway, so blocking it costs no margin at
   all (§6).

A rupee model that scores 7,240 wrongly-blocked legitimate transactions at ₹8.6k
is telling you something about the model, not about the detector. **Do not read
the low cost as evidence that this failure is unimportant.** A merchant whose
customers are being blocked during an issuer outage does not experience it as an
₹8,595 event, and none of the reputational cost is represented anywhere in
`costs.toml`.

**This flaw does not stop at the reported figures — it was also choosing the
operating point.** Until the addendum, the threshold was selected by maximising
net rupees on this same model, so a model that prices false positives at almost
nothing was deciding how many false positives to accept. That is why §0.1 replaced
the criterion with a constrained one rather than only annotating the numbers.

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

> **PITCH-VIDEO CANDIDATE — the "what broke" beat.** This section is the most
> credible thing in the document, and the reason is not the ablation result. It is
> that a finding was withdrawn because three seeds could not resolve it against
> its own variance, and the opposite finding was refused on the same grounds. The
> discipline this whole submission argues for is exactly this: a number you cannot
> defend is not a result, whichever way it points.

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
- **The multi-seed spread at the constrained operating point.** §0's spread table
  is from the unconstrained point; it is retained because the §3 retraction rests
  on it. Regenerating it at the constrained point is a Phase 3 gate item.
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

**The break-even review cost is ₹985 per alert** at the constrained operating
point (312 alerts, 6.2/day). It was ₹204 at the unconstrained point, where alert
volume was 1,379. That is the figure to quote, because it is an output rather than
an assumption: the detector pays for itself as long as reviewing an alert costs
under ₹985.

That the constraint *raised* break-even by 5× is worth stating plainly — capping
the alert queue did not trade money for sanity, it improved both. The
unconstrained point was not on the efficient frontier at all.

---

## 7. Recommendations arising from Phase 2

Ordered by how much they change the credibility of the submission:

1. **Give the detector a long-horizon window** on the BIN axis so retry structure
   is visible at the scale it actually occurs (§2.2). This is the single change
   most likely to fix §2.1, which is the worst result in the project.

   **Diagnosed, not attempted, and here is why.** Phase 3 ports this detector to
   Rust and tests numerical parity against it, so `reference.py` is the parity
   spec. Changing the reference moves the spec. At day 8 of 11, with a
   pre-committed one-day hard stop on parity work already load-bearing, adding a
   second window to the BIN axis is a redesign of the scoring function rather
   than a parameter change — and it would land in the same 24 hours as the port.

   The detector is **frozen** from the Phase 2 gate through Phase 3 for that
   reason. This is the honest version of the trade: the fix is identified, the
   mechanism is understood (§2.2 works out why a 5-minute window sees 1.13
   in-window attempts per card for an outage against 1.22 for a burst — the
   damping term points the wrong way), and there was not enough runway to build
   and re-validate it without putting the port at risk.
2. **Model alert fatigue, or stop reporting net rupees as the deciding metric**
   (§3.1). Linear review cost is why the cost model cannot separate the variants.
3. **Reduce variance before running ablations again** — common random numbers
   across variants, or more seeds. §3 is currently a null result for measurement
   reasons, not architectural ones.
4. **Persist baselines across restarts** to remove the cold-start failure (§2.3).
