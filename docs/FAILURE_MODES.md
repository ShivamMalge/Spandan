# Failure modes

What Spandan misses, what it over-flags, and where its numbers cannot be trusted.
Every claim is backed by a measurement from `make eval` or `spandan replay`, not
by inspection of the design.

State: final (Phase 6). Stream is 100 days, 1.61M events, 240 episodes (20 per
scenario per split), three seeds. The detector froze at the end of Phase 2 so
the Rust port had a stable parity spec, and **never unfroze** — so the numbers
measured then are the final numbers, re-confirmed byte-identically by the
Phase 4 engine swap (`make eval ENGINE=rust` vs `python`, only the engine
label differs). §8 was added in Phase 5-6 for the explanation layer.

---

## The headline

**Precision at a realistic merchant card-testing base rate (0.15%, assumed) is
0.0824 — roughly eleven false alarms for every true catch.**

That is the number a payments panel will look for, so it is the number this
document and `make eval` both open with. Measured at the generator's own 1.33%
positive rate precision is 0.4462, and that figure flatters the detector by about
an order of magnitude.

**And a flag declines the transaction** (*What a flag does*, above), so the figure that decides
deployability is not precision but the decline rate on legitimate traffic:
**1.41%, or 1 in 71 legitimate customers.**

Against that: every one of the 60 attack episodes in the test window is caught,
recall is 0.844, and the median episode is caught on its first or second event.

The gap between those paragraphs is the honest summary of where this project
stands: **it detects reliably and fast, and it is not deployable as an inline
control**, because of the failure mode in §2.1 and the decline rate that follows
from it. Both halves are measured; neither is softened.

*(Figures throughout are from the 600-point threshold grid, seed 20260824, at the
constrained operating point. An earlier 60-point grid gave slightly different and
more flattering numbers; those are marked superseded where they appear.)*

---

## What a flag does, and why the answer changes how this document reads

**A flag declines the transaction it is raised on.** It is an inline
authorization control. Alerts are the human-facing grouping of those declines per
(merchant, BIN) — the surface an analyst reviews — not a separate, softer action.

This was ambiguous through Phase 2, and the ambiguity was load-bearing. The
report constrained the operating point on **alerts per day**, which measures
analyst workload, while the cost model charged blocked-transaction value **per
event**, which is only coherent if flags block. Read one way, capping alerts
looked like capping merchant impact. It is not, and the gap is not small:

> At the headline operating point, **20,254 flagged events collapse into 487
> alerts** — 42 events per alert. An alert budget bounds the review queue. It
> says nothing about how many customers were declined.

So the number that decides deployability is not alerts/day. It is the share of
**legitimate** transactions the control declines:

| budget | alerts/day | legitimate transactions declined | one in |
|---|---|---|---|
| 2 | 2.1 | **0.37%** | 271 |
| 5 | 4.7 | 0.93% | 108 |
| 10 (headline) | 9.7 | **1.41%** | **71** |
| 50 | 18.3 | 1.62% | 62 |

**At the headline operating point the detector declines roughly 1 in 71
legitimate transactions.** For scale: Datos Insights puts the average
e-commerce false-decline rate — "the fraudulent decline of a valid transaction"
— at **1.51% of e-commerce sales**, "representing lost e-commerce revenue of
nearly US$265 billion by 2027" (*E-Commerce Fraud Landscape and Trends:
Merchants Seeking to Adapt*, Mattei and Inhofe, May 2024, from a survey of 200
U.S. and U.K. merchants; published via Visa Acceptance Solutions). That figure
is dollar-weighted, this one is per transaction, and the survey is not Indian —
so they are not the same unit or market. But at 1.41% this detector alone would
add roughly the entire industry average again on top of whatever a merchant
already loses to false declines. It is not a rounding error; it is a comparable
amount. **This detector is not deployable as an inline control
at any alert budget**, and no tightening of the alert queue fixes it, because the
alert queue was never what was hurting the merchant.

Choosing the other reading was available: flags could notify only. That would be
equally defensible in the abstract and it would invalidate **both** sides of the
rupee model — nothing is prevented until a human acts, so neither the
blocked-good cost nor the avoided-chargeback saving could be claimed without a
response-time model this project does not have. The choice is recorded in
`detect/interface.py` under "WHAT A FLAG DOES", so the code states its own
semantics rather than leaving them to be inferred from the cost model.

The consequence for everything below: **`declined` and `flag rate` are reported
beside `alerts/day` everywhere either appears**, and the cost model's blocked-good
and avoided-chargeback terms are correct as written, because both assume the
attempt was stopped.

---

## 0. What the multi-seed check settled, and what it did not

Nothing in this document should be read from a single stream. Every headline
below is a median across three independently generated streams, with the range.

At the constrained operating point, across three streams:

| Metric | min | median | max | spread |
|---|---|---|---|---|
| precision | 0.4117 | **0.4462** | 0.5972 | 0.186 |
| recall | 0.8189 | **0.8444** | 0.9106 | **0.092** |
| PR-AUC | 0.6192 | 0.6615 | 0.7928 | 0.174 |
| net rupees | ₹279,151 | ₹348,845 | ₹395,007 | ₹115,856 |
| alerts/day | 3.8 | 9.7 | 10.8 | 7.0 |
| headroom % | −3.255 | −2.674 | −2.397 | 0.858 |

Per seed, which says *where* the instability is:

| seed | threshold | PR-AUC | precision | recall |
|---|---|---|---|---|
| 20260824 | 21.99 | 0.6615 | 0.4462 | 0.8444 |
| 20260825 | 22.05 | 0.6192 | 0.4117 | 0.9106 |
| 20260826 | 24.53 | 0.7928 | 0.5972 | 0.8189 |

**Recall is far more stable than it was; precision still is not.** Recall's spread
fell from 0.386 to 0.092 when the test window went from 6 attack episodes to 60 —
that was the underpowered-evaluation problem, and it is largely fixed. Precision
still swings 0.41–0.60 because it is dominated by false positives on one control
(§2.1), whose volume varies with the stream.

PR-AUC is threshold-free, so its 0.174 spread is the detector and the data rather
than the operating point. Selected thresholds ranged 21.99–24.53, much tighter
than the 25.90–39.09 seen before the test window was properly powered.

**Alerts/day ranges 3.8 to 10.8** under the constraint — far tighter than the
3.9–27.6 the unconstrained criterion produced, which is the budget doing its job
on the axis it actually governs. It governs only that axis; see §0.

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
| threshold | 21.43 | **21.99** |
| alerts/day | 18.3 | **9.7** |
| precision | 0.418 | **0.446** |
| precision @ 0.15% | 0.0740 | **0.0824** |
| recall | 0.862 | 0.844 |
| episodes caught | 60/60 | 60/60 |
| flag rate | 0.0274 | **0.0252** |

Figures are from the 600-point grid. An earlier 60-point grid reported a larger
improvement (precision 0.400 → 0.487) because its coarse sampling happened to
place the constrained pick at a favourable threshold; the refined grid finds a
higher-validation-net point inside the same budget, and it generalises worse. The
coarse figures were luck and are superseded.

The frontier, printed by `make eval` as a sensitivity table (600-point threshold
grid; the first version used 60 and left the whole 6–28 alerts/day band unsampled):

| budget | thresh | alerts/d | ev/alert | flag rate | prec | prec @ 0.15% | recall | episodes | med TTD | val net | test net |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | 27.99 | 2.1 | 90 | 0.0120 | 0.696 | **0.2034** | 0.627 | 35/60 | 6 | ₹82,806 | ₹324,539 |
| 5 | 23.86 | 4.7 | 66 | 0.0191 | 0.519 | 0.1073 | 0.745 | 58/60 | 5 | ₹100,588 | ₹322,004 |
| 8 | 22.36 | 8.3 | 46 | 0.0238 | 0.462 | 0.0875 | 0.828 | 60/60 | 1 | ₹114,196 | ₹284,112 |
| **10** | **21.99** | **9.7** | **42** | **0.0252** | **0.446** | **0.0824** | **0.844** | **60/60** | **0** | **₹119,267** | **₹279,151** |
| 12 | 21.80 | 11.6 | 36 | 0.0259 | 0.437 | 0.0797 | 0.850 | 60/60 | 0 | ₹117,924 | ₹272,040 |
| 15 | 21.61 | 14.2 | 30 | 0.0266 | 0.428 | 0.0769 | 0.856 | 60/60 | 0 | ₹117,030 | ₹267,910 |
| 20 | 21.43 | 18.3 | 24 | 0.0274 | 0.418 | 0.0740 | 0.862 | 60/60 | 0 | ₹114,877 | ₹254,638 |
| 30 | 21.43 | 18.3 | 24 | 0.0274 | 0.418 | 0.0740 | 0.862 | 60/60 | 0 | ₹114,877 | ₹254,638 |
| 50 | 21.43 | 18.3 | 24 | 0.0274 | 0.418 | 0.0740 | 0.862 | 60/60 | 0 | ₹114,877 | ₹254,638 |

Three things this table says, and the second is a finding about the constraint
rather than about the detector.

**Net is not monotone in the budget on test, and that is not a bug.** A larger
budget permits every threshold a smaller one permits, so the constrained maximum
cannot fall — and on the **validation** window, where selection happens, it does
not: ₹82,806 → ₹100,588 → ₹114,196 → ₹119,267, rising monotonically. The `test
net` column is measured at a threshold chosen on validation, so the two need not
agree. Where test net falls as the budget loosens, that is the
validation-to-test generalisation gap. It is worth reading as evidence: **the
operating point that looked best on validation transferred worst.**

**An alert budget does not constrain event-level over-triggering.** `ev/alert` is
how many flagged events each alert collapses. Alerts are deduplicated per
(merchant, BIN) with a 15-minute cooldown, so at the headline budget **20,254
flagged events — 2.5% of all traffic — collapse into 487 alerts.** The flood hides
inside the dedup. This is why `med TTD` returns to 0 at every budget of 10 or
looser: episodes are caught on their first event because nearly everything is
being flagged. The alert budget was chosen as the constraint because a merchant
can state it from their own staffing; that reasoning stands, but on this evidence
it is a **weak proxy for how noisy the detector actually is**, and `flag rate`
and `declined` now sit beside it everywhere.

A joint constraint — alerts/day *and* an event-level flag-rate cap — is the
obvious follow-up. **It was deliberately not added.** Registering a second
constraint now, after these numbers are known, is exactly the selecting-on-test
this document spends §0.1 avoiding; doing it honestly means stating the basis
first and then measuring, and that costs more than the schedule has. It is
recorded in §7 as diagnosed-not-attempted, alongside the long-horizon BIN
window.

**Net rupees varies 1.3× across the whole frontier (₹254k–₹324k) while precision
at a realistic base rate varies 2.7× (0.074–0.203).** The cost model cannot see
the difference that matters most. This is the §6 limitation appearing in the place
it does the most damage, and it is the single sharpest finding in this document.

> **PITCH-VIDEO CANDIDATE.** "The cost model is nearly indifferent across the
> entire operating range, while the number a merchant would actually live with
> moves by a factor of three. A rupee figure that cannot distinguish the good
> operating point from the bad one is not a decision procedure — which is why the
> threshold is now chosen under a constraint the merchant states, not by
> maximising the model."

**A note on why the headline row is not the best row.** Budgets 2 and 5 are better
on test net *and* on precision. The headline stays at 10 because that budget was
registered in `costs.toml` before the test window was read, and its basis — what
one analyst can work through — does not depend on these results. Moving it now
would be selecting on the test set, which is the thing this table's caption warns
against. Re-registering the budget is a legitimate Phase 6 decision **provided it
is argued from the operational basis and not from this column.**

This is a **sensitivity analysis, not a menu.**

**The configuration this project would ship is not the headline row.** Inline
blocking is not deployable at any budget (*What a flag does*). Alert-only at
budget 2 is: 2.1 alerts/day, event precision 0.696, precision 0.2034 at a 0.15%
base rate, 35 of 60 episodes surfaced, and 1 in 271 legitimate transactions
flagged rather than 1 in 71. It is chosen from this table on its operational
basis — what one analyst can review with attention — and not on its test column.
The caveat stands and is the reason it was never the headline: with flags
notifying rather than declining, neither the blocked-good cost nor the
avoided-chargeback saving can be claimed, because nothing is prevented until a
human acts and this project has no response-time model. The rupee figures in
this document do not transfer to it. What transfers is the detection: 58% of
campaigns in front of a human at two items a day.

---

## 1. Detection speed, at the constrained operating point

| Scenario | episodes caught | median events before first flag | p90 | median rupees exposed |
|---|---|---|---|---|
| `burst` | **20/20** | 0 | 32 | ₹26 |
| `rotating` | **20/20** | 0 | 6 | ₹0 |
| `slow_low` | **20/20** | 1 | 7 | ₹11 |

Every episode of every attack scenario is caught, essentially immediately.

**Read that with suspicion, because a median of 0 events is exactly what
over-triggering looks like.** Catching every episode on its first event is
trivially achievable by flagging almost everything, and at this operating point
the detector flags **2.52% of all traffic**. The alert budget did not prevent
this: dedup collapses 42 flagged events into each alert, so an operating point
that floods at the event level still sits inside a 10-alerts-per-day cap
(*What a flag does*).

The p90 column is the more informative one, because it is not saturated: 32 events
for `burst`, 6 for `rotating`, 7 for `slow_low`. Detection speed is genuinely
good — the burst p90 of 32 events on episodes of 190–300 events is a real result —
but the median is not evidence of anything on its own.

An earlier addendum reported 2/4/2 median events from a 60-point grid. Those
figures are superseded: the finer grid selects a lower threshold inside the same
budget, and the medians saturate to 0. The honest reading is that **the median was
never the number to quote here**; p90 is.

**`slow_low` is no longer the weak scenario**, but read that carefully. It caught
1 of 2 episodes at 1.7% event recall in Phase 2 and now catches 20/20. None of
that came from a change to the detector — the detector is unchanged. It came from
the operating point moving and from the test window growing from 2 episodes to 20.
Its event-level recall is still the lowest of the three at 0.445
(burst 0.962, rotating 0.825).

Event-level recall is 0.844 overall. Episode-level detection is 60/60.

**Three different precisions, all reported, because they answer different
questions:**

| | value | what it answers |
|---|---|---|
| event-level | 0.446 | of flagged transactions, how many were card testing |
| alert-level | **0.433** | of the 487 alerts a human opens, how many are real (211) |
| at 0.15% prevalence | **0.0824** | what either would be at a realistic base rate |

Alert-level precision is what the analyst experiences and is almost never
reported. Base-rate-adjusted precision is what a merchant would live with. Neither
replaces the other, and quoting only the first would be the flattering choice.

---

## 2. What it over-flags

### 2.1 The single-merchant issuer outage. This is the headline failure.

At the constrained operating point (threshold 21.99):

| Control | axis attacked | events | flagged | rate | blocked-good cost |
|---|---|---|---|---|---|
| `flash_sale` | volume | 17,787 | 20 | 0.0011 | ₹350 |
| `issuer_outage` | decline ratio | 18,057 | 1,818 | 0.1007 | **₹2.03L** |
| **`outage_single_merchant`** | decline ratio, no crutches | 18,169 | **9,170** | **0.5047** | ₹11,077 |
| `benign` | — | 740,349 | 208 | 0.0003 | ₹37,847 |

The `flash_sale` control is close to clean (20 events of 17,787, 0.11%). The volume
axis is handled. The decline-ratio axis is not.

**At the constrained operating point, 50.5% of a legitimate single-merchant issuer
outage is flagged as card testing** (9,170 of 18,169 events).

**The negative headroom is the finding, not a failure to report.** The
highest-scoring clean event scores 80.79 against a threshold of 21.99 — headroom
−267.4% — on every seed. What that identifies precisely: the detector has a blind
spot on *legitimate traffic whose declines are concentrated on one BIN at one
merchant*. It cannot separate that from card testing, because the one feature that
would — the same card retried — is invisible at the window size it uses (§2.2).

The control was built to find exactly this, and it found it. A negative-headroom
result from a purpose-built control is the control working, and it is a sharper
statement about the detector than any precision figure: not "it makes mistakes at
this rate" but "there is a specific, common, nameable class of legitimate traffic
it cannot see the difference from.".

**And notice what the rupee column does with that.** The worst failure mode by
count — 9,170 false positives — costs ₹11,077, while a control flagged five times
less often costs ₹2.03L. Two reasons, both of which are limitations of the cost
model rather than mitigations:

1. `outage_single_merchant` carries low-value baskets by construction, so the
   contribution margin lost per blocked transaction is small.
2. Most of its traffic was declining anyway, so blocking it costs no margin at
   all (§6).

A rupee model that scores 9,170 wrongly-blocked legitimate transactions at ₹11k
is telling you something about the model, not about the detector. **Do not read
the low cost as evidence that this failure is unimportant.** A merchant whose
customers are being blocked during an issuer outage does not experience it as an
₹11,077 event, and none of the reputational cost is represented anywhere in
`costs.toml`.

**This flaw does not stop at the reported figures — it was also choosing the
operating point.** Until the addendum, the threshold was selected by maximising
net rupees on this same model, so a model that prices false positives at almost
nothing was deciding how many false positives to accept. That is why §0.1 replaced
the criterion with a constrained one rather than only annotating the numbers.

The headroom is negative on every seed: the
highest-scoring clean event scores **80.79** against a threshold of **21.99**. The
control is not being rejected at all. It is being scored like an attack, and the
only thing separating the two populations is where the line happens to fall.

This was built deliberately, on review instruction, after Phase 2 measured *which*
separator was doing the work on the multi-merchant outage. The answer was that the
detector was rejecting it on **merchant span and amount** — not on the retry
structure the control was designed around. `outage_single_merchant` removes both
crutches: one merchant, and amounts in the same low band as a probe burst.

What is left is retry structure, and **the detector cannot see it** (§2.2).

Consequences, stated plainly:

- Precision at the observed prevalence is 0.4462. At the stated realistic
  0.15% prevalence it falls to **0.0824**.
- A merchant running this detector during an issuer outage would have most of
  their legitimate declining traffic flagged, at the worst possible moment.
- This is a real, common, well-understood payments event. It is the first
  false-positive case a risk panel raises, and the honest answer today is that
  the detector fails it.

### 2.1a With the triage graph: a routing result, not a detector result

The detector is frozen and every score above is unchanged. What changed is
what a flag *becomes*. `python/spandan/triage/` routes each flag through an
explicit graph (`docs/ARCHITECTURE.md` step 4), and one node — the
kill-switch — turns inline decline into alert-only for a single (merchant,
BIN) when the trailing hour shows the retry structure of an outage: attempts
per distinct card ≥ 2.5 over ≥ 20 events, holding for an hour. The
parameters were chosen on the **training** window and registered in
`costs.toml` before this table existed (basis there); the test result was
not known when the value was set, and the value was not moved afterwards.

| test window, seed 20260824 | raw (every flag declines) | through the graph |
|---|---|---|
| `outage_single_merchant` flagged → declined | 9,170 → 9,170 | 9,170 → **5,723** (3,447 alerted) |
| kill-switch trips | — | **20**, all on `outage_single_merchant`, none on any attack |
| legitimate transactions declined | 1.41%, **1 in 71** | 0.98%, **1 in 102** |
| false positives that declined | 11,216 | **7,769** |
| true positives that declined | 9,038 | **9,038** — unchanged |
| burst / rotating / slow_low flagged → declined | 4,514 / 4,014 / 510 | 4,514 / 4,014 / 510 — unchanged |
| blocked-good cost | ₹252,220 | ₹248,119 |
| gross before review | ₹298,631 | ₹302,732 |

Three things this table says.

**The switch fires on exactly the traffic it was registered for.** Twenty
trips, one per single-merchant outage episode, and not one on the sixty
attack episodes. Every attack event the detector flagged still declines, so
recall through the action is untouched — a count-based switch would have hit
the bursts first; the retry ratio does not.

**It is a recovery, not a fix, and it is late by construction.** 3,447 of
11,216 false declines — 31% — become alerts. The rest decline before the
switch can see them: it needs enough of the hour to accumulate for the
retry ratio to clear 2.5 on ≥ 20 events, so the opening of each outage
episode is declined regardless. 1 in 102 is materially better than 1 in 71
and it is still not deployable as an inline control. The headline stands.

**The rupee model barely notices, for the reason §6 gives.** Blocked-good
cost moves ₹4,101 on 3,447 recovered declines, because outage traffic was
mostly going to decline anyway and the model charges no margin for it. The
cost model cannot see this improvement; the decline rate can. Which is one
more reason the decline rate, not the rupee figure, is the deployability
number.

This is the §7 recommendation-1 signal — retry structure over a long
horizon — placed in the routing layer instead of the scoring function. It
does what a routing rule can do with it. What it cannot do is score with
it: a detector that saw the retry ratio inside its own window would flag
less rather than decline less. That fix is now measured as an experiment
(§7): 35.6% of the outage flagged at the hand weight, 18.0% at five times
it, against 50.5% — and it fails its registered ship gate on the outage
condition, so it stays out of the detector.

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

## 3. The ablations: retracted once, then re-measured at a different operating point

> **PITCH-VIDEO CANDIDATE — the "what broke" beat.** This section is the most
> credible thing in the document, and the reason is not any ablation result. It is
> that a finding was withdrawn because three seeds could not resolve it against its
> own variance, the opposite finding was refused on the same grounds, and when the
> operating point changed the measurement was run again rather than the old
> conclusion being carried forward. A number you cannot defend is not a result,
> whichever way it points.

**The history matters, so it is written out rather than summarised.**

**Phase 2 claimed** that dropping the per-entity EWMA baseline improved net
position by ₹17,159 and that EWMA "is not carrying the detection signal". Measured
on one stream.

**That claim was retracted.** Across three seeds at the *unconstrained* operating
point the delta was ₹12,981 median with a range of [−₹43,496, +₹109,443] — it
changed sign across streams. The opposite claim, that EWMA is vindicated, was
refused on the same evidence. The result was reported as a null one.

**At the constrained operating point the measurement comes out differently, and
this document reports that rather than carrying the null forward.** Median across
3 streams, with [min, max]:

| variant | precision | recall | net rupees |
|---|---|---|---|
| full | **0.446** [0.41, 0.60] | 0.844 [0.82, 0.91] | 348,845 [279,151, 395,007] |
| drop-EWMA | 0.367 [0.37, 0.40] | 0.793 [0.70, 0.81] | **381,778** [346,266, 410,499] |
| drop-per-IP | 0.459 [0.42, 0.67] | **0.855** [0.81, 0.91] | 363,661 [307,025, 410,567] |

Paired per seed, which is the only comparison that controls for the stream:

| ablation | net delta vs full (median) | range | beats full on | verdict |
|---|---|---|---|---|
| drop-EWMA | +₹32,933 | [+₹15,492, +₹67,115] | **3 of 3 seeds** | **consistent** |
| drop-per-IP | +₹15,560 | [+₹14,816, +₹27,874] | **3 of 3 seeds** | **consistent** |

So on net rupees, **both ablations consistently beat the full detector.** The
delta no longer changes sign. That is a real change from the null result, and it
came from moving the operating point, not from new data.

**What that does and does not license.**

It does *not* license "the EWMA baseline is useless", for a reason established
earlier in this document and before this table was produced: **§0.1 shows net
rupees varies 1.3× across the entire operating range while precision at a
realistic base rate varies 2.7×.** Net rupees is the metric already demonstrated
to be nearly blind to the difference that matters. Winning consistently on a
metric that cannot see the important axis is weak evidence, however consistent it
is.

On the axis that can see it, the ordering reverses: **drop-EWMA costs precision
consistently** — 0.367 median against full's 0.446, and its range [0.37, 0.40]
barely overlaps full's [0.41, 0.60]. It also costs recall (0.793 vs 0.844). It
buys money by declining more traffic, which is the same trade *What a flag does*
says the merchant
pays for.

`drop-per-IP` is the genuinely awkward one: it beats full on net (consistently),
on precision (0.459 vs 0.446) and on recall (0.855 vs 0.844). The medians are
close and the ranges overlap heavily, so this is not a demonstration that the
per-IP axis is harmful — but there is **no evidence in this table that it helps**,
and the honest statement is that the per-IP axis is unsupported by the ablation
rather than validated by it.

**Net of all three passes:** the per-entity EWMA baseline is supported on
precision and unsupported on rupees; the per-IP axis is unsupported on both. No
component is vindicated here, and the write-up will not claim one is.

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

**Recommendation:** argue for the full configuration on precision (0.446 vs
drop-EWMA's 0.367, ranges barely overlapping) and on decline rate, and state
plainly that **both ablations beat it on net rupees, consistently, across every
seed.** Then say why that is not the deciding evidence: net rupees is the metric
§0.1 shows cannot distinguish the good operating point from the bad one, and a
component that earns money by declining more legitimate customers is not earning
it in a way the merchant would choose.

State the per-IP result as unsupported rather than harmful, and do not present any
component as vindicated. The ablation table vindicates nothing; claiming otherwise
is the exact failure this project exists to avoid.

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
- ~~No latency or throughput numbers.~~ Measured in Phase 4 — `docs/BENCH.md`.
- **Cold-start cost is counted, not costed** — the replay path does not run the
  cost model.
- **Variance reduction was not attempted.** §3's null result is a consequence.

---

## 6. Cost-model sensitivities

Gross ₹2.99L on the headline seed: avoided chargeback exposure ₹5.37L, saved
authorization fees ₹13,557, blocked good transactions −₹2.52L.

Two things worth noticing:

- **9,397 of the 11,216 blocked clean transactions were going to decline
  anyway**, and so cost the merchant no margin. That is the outage controls
  showing up in the cost model exactly as intended — flagging a declining
  transaction is cheap in rupees even when it is wrong. It is *not* cheap in
  trust, and the cost model does not capture that.
- The net position is dominated by the chargeback term, which is the product of
  two uncitable assumptions (a ₹500 dispute fee, an 0.8 chargeback rate on
  approved fraud). Halving the assumed rate roughly halves the headline saving.

**The break-even review cost is ₹613 per alert** at the constrained operating
point (487 alerts, 9.7/day). That is the figure to quote, because it is an output
rather than an assumption: the detector pays for itself as long as reviewing an
alert costs under ₹613.

Read it next to *What a flag does* rather than on its own. Break-even counts only what the review
queue costs; it prices none of the 11,216 legitimate transactions declined, whose
cost to the merchant the model puts at ₹2.52L and whose cost in customer trust it
does not model at all.

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

   **Partially realised in routing, Phase H.** The same signal — attempts per
   distinct card over a trailing hour — now drives the triage graph's
   kill-switch (§2.1a): 20 of 20 single-merchant outage episodes trip it,
   no attack does, and the legitimate-decline rate moves from 1 in 71 to
   1 in 102 without touching a score. That is what the signal can do as a
   routing rule. The detector-level fix, which would stop the flags rather
   than the declines, is still unbuilt.

   **Registered before measurement, 2026-09-03 (Phase E part 1).** The fix is
   built as an experiment, `spandan.detect.experimental.LongHorizonDetector`,
   a subclass that leaves `reference.py` and the Rust core untouched and is
   reported, not shipped. Parameters fixed before any run on the test
   window: a 60-minute window on the BIN axis; it feeds the `repetition`
   term and nothing else; two weights, 1.2 (the hand weight on the
   five-minute term) and 6.0 (five times, the multiplier the section 9 linear
   model learned for this term on the warm-up window); thresholds chosen on
   validation under the same alerts/day ≤ 10 budget; three seeds. The
   ship gate is in IMPROVEMENT_PHASES Phase E and does not change with the
   result. The result goes here, whatever it is.

   **Measured, 2026-09-03, on the stream every other figure comes from**
   (`make experiment`; thresholds chosen on validation under the same budget;
   the frozen rows inside each run reproduce `make eval` exactly):

   | detector | outage_single_merchant flagged | precision @ 0.15% | precision | recall | PR-AUC | legitimate declined | threshold | alerts/day (test) |
   |---|---|---|---|---|---|---|---|---|
   | frozen (5-minute repetition, weight 1.2) | 9,170/18,169 (**50.5%**) | **0.0824** | 0.4462 | 0.8444 | 0.6615 | 1.41%, 1 in 71 | 21.99 | 9.7 |
   | long_horizon (60-minute repetition, weight 1.2) | 6,463/18,169 (**35.6%**) | **0.1071** | 0.5183 | 0.8413 | 0.6780 | 1.05%, 1 in 95 | 21.80 | 10.1 |
   | long_horizon_x5 (60-minute repetition, weight 6.0) | 3,268/18,169 (**18.0%**) | **0.1658** | 0.6406 | 0.7969 | 0.6983 | 0.60%, 1 in 166 | 21.59 | 10.2 |

   Per scenario, share of events flagged:

   | detector | burst | rotating | slow_low | flash_sale | issuer_outage |
   |---|---|---|---|---|---|
   | frozen | 0.9619 | 0.8252 | 0.4446 | 0.0011 | 0.1007 |
   | long_horizon | 0.9619 | 0.8183 | 0.4455 | 0.0012 | 0.0920 |
   | long_horizon_x5 | 0.9538 | 0.7385 | 0.4028 | 0.0012 | 0.0690 |

   Across three independently generated streams (median, min–max):

   | detector | precision | recall | net ₹ |
   |---|---|---|---|
   | frozen | 0.4462 (0.4117–0.5972) | 0.8444 (0.8189–0.9106) | 348,845 (279,151–395,007) |
   | long_horizon | 0.5183 (0.4752–0.6509) | 0.8413 (0.8215–0.9089) | 369,094 (297,371–396,844) |
   | long_horizon_x5 | 0.6406 (0.6024–0.7324) | 0.8368 (0.7969–0.8786) | 406,857 (330,854–408,988) |

   **What it says.** The diagnosis holds: one term, fed from a window long
   enough to see the retry structure, removes two-thirds of the outage false
   positives (50.5% → 18.0%) and doubles precision at the realistic base rate
   (0.0824 → 0.1658) at the same alert budget, without touching the other five
   terms. At the hand weight the window alone is worth 15 points of outage and
   a third more precision at the base rate, with recall unchanged. The
   five-fold weight, which the section 9 linear model had learned, buys the
   rest of the outage reduction with recall: 0.8444 → 0.7969, 4.7 points, most
   of it on the rotating attack (0.8252 → 0.7385), whose card rotation is the
   attack that most resembles retrying. The issuer-wide outage moves the same
   way (10.1% → 6.9%); the flash sale is untouched.

   **Against the gate registered in IMPROVEMENT_PHASES Phase E.** Ship only if
   the outage falls below 15%, precision at the base rate rises above 0.15,
   the legitimate-decline rate falls below 0.7%, and it is before 10:00 on
   Sep 5. The hand weight fails all three numeric conditions. The five-fold
   weight passes two (0.1658 > 0.15; 0.60% < 0.7%) and fails the outage
   condition, 18.0% against 15%. **Not shipped.** The detector stays frozen and
   every headline figure in this repository is the frozen one. A weight
   between 1.2 and 6.0, chosen on validation, might clear 15% at a smaller
   recall cost; it was not tried, because the two weights were the ones
   registered and a third chosen after reading these tables would be the
   selection-on-test this document exists to avoid. That search, the Rust
   port, and parity regeneration are the next project, and this table is what
   it starts from.
2. **Model alert fatigue, or stop reporting net rupees as the deciding metric**
   (§3.1). Linear review cost is why the cost model cannot separate the variants.
3. **Add an event-level flag-rate constraint** alongside the alerts/day budget,
   as a *joint* constraint. The alert budget alone does not constrain
   over-triggering, because dedup collapses 42 flagged events into each alert
   (*What a flag does*). A cap on the share of legitimate traffic declined would
   bind where the
   alert budget does not.

   **Diagnosed, not attempted, and here is why.** It is a change to the selection
   rule rather than the detector, so the freeze does not block it. What blocks it
   is method: registering a second constraint *after* seeing which budgets score
   well on test is the selecting-on-test this project spends its credibility
   avoiding. Doing it honestly means stating the cap and its operational basis
   first, then measuring once — and that sequence costs more than the remaining
   schedule has. The number it would constrain is reported prominently instead.
4. **Reduce variance before running ablations again** — common random numbers
   across variants, or more seeds. §3 is currently a null result for measurement
   reasons, not architectural ones.
5. **Bound total memory with entity eviction or a sketch.** Entities are never
   freed, so total memory is linear in distinct entity count: measured at 4,819
   bytes/entity (Rust) and 1,971 (Python) under high-cardinality churn, which
   projects to **38.6 GB / 15.8 GB per month** at an assumed 8M distinct entities
   (`docs/BENCH.md` §4). Two candidate fixes, neither built: **LRU eviction** of
   cold entities (loses long-idle baselines — an entity returning after eviction
   is cold-started, re-opening §2.3's failure mode for exactly the rarely-seen
   entities), or a **count-min sketch** for the velocity counts (hard memory cap;
   over-counts on hash collisions, which inflates velocity evidence and costs
   precision — the error is one-sided in the unsafe direction). Both are design
   changes to frozen state machinery. Until one exists the deployment statement
   is "restart or shard before the entity table exceeds memory."

   **The Rust-vs-Python 2× gap itself is closable, and here is how.** Two causes,
   both addressable: the ring pre-allocates 64 slots per entity (a churn entity
   that sees one event pays for 64), and each retained slot owns two heap
   `String`s where Python shares references. The fixes are mechanical — grow the
   ring from zero, and intern entity identifiers to `u64` handles in a
   per-detector table so slots store 8-byte ids instead of owned strings; the
   second also speeds up every hash lookup. Estimated to bring Rust at or below
   Python's 1,975 bytes/entity. Not done because both touch frozen state
   machinery mid-schedule for a constant factor, while the O(entities) growth —
   the actual deployment blocker — is untouched by either. Closing a 2× constant
   on an unbounded curve is polish, not the fix.
6. **Persist baselines across restarts** to remove the cold-start failure (§2.3).

## 8. The explanation layer hallucinates. The boundary is why that is survivable.

**What was measured.** Phase 5's comparison protocol: a hand-written target
explanation was committed before any LLM code existed, then the model's output
was recorded over the wire (`gemini-3.1-flash-lite`, 2026-08-26, cassettes
committed exactly as returned) and judged against it. The result is a negative
finding, reported here the way the ablation retraction was: as a result, not
an embarrassment.

**The model fabricates evidence.** On the ₹5.45 probe, its only decision rule
is "Block the BIN for 24 hours if the CVV/AVS result on this attempt returned
'Mismatched' or 'Not Supported'" — no CVV or AVS field exists in the `Flag`,
the prompt, or anywhere in this pipeline. On the ₹150 case it orders "If no
successful prior history exists at this merchant, blacklist the card and
cardholder IP immediately" — per-card history and cardholder IP are equally
absent from the evidence it was given. Both next-actions are conditioned on
data the analyst does not have. This is the worst failure shape for an
analyst-facing note: not vagueness but confident, specific, *ungrounded*
grounds for a block. The prompt explicitly said "the evidence below is
everything known", and the fabrication happened anyway — prompt discipline is
not a boundary, which is precisely why this project built a structural one.

**It also misrepresents the detection basis.** Both notes narrate a
single-credential causal story ("a bot testing a single stolen credential")
when the detector actually fires on velocity and decline-ratio deviation
across an entity's sliding window against learned baselines. One note
attributes the BIN's baseline ticket to the merchant. An explanation that
misstates what was measured teaches the analyst a wrong model of the alarm.

**Why this cannot touch a number.** The blast radius of a hallucinating
explainer here is prose, by construction, and the construction is tested:

- `spandan.detect` and `spandan.eval` cannot import `spandan.llm` — the
  import-graph test fails on the first edge.
- The full evaluation runs to completion with `sys.modules['spandan.llm']`
  replaced by an object that raises on any attribute access, producing
  bit-identical scores and the identical selected threshold (the
  poisoned-import test). No number in this document passed through a language
  model — not "we didn't", but "we structurally could not have".
- `Flag` is frozen; the explainer receives copies of fields and has no write
  path back into evidence, scores, thresholds, or labels.
- Replay mode never opens a socket — enforced in tests at the socket layer.

This is the boundary earning its keep. Had the explainer been allowed near
the decision path — the "LLM triage" shape — the fabricated CVV/AVS rule
would be a fabricated *blocking criterion*. Here it degrades one analyst
note, is caught by reading the note against the schema, and corrupts nothing.

**Disposition.** The cassettes stay as recorded; re-prompting for a nicer
sample after seeing the bad one is the same selection error this project
refuses on thresholds.

**The fix, built and measured (Phase D, 2026-09-03).** `llm/grounding.py`
validates a note against the rendered prompt — exactly what the model was
shown — on the simplest rule that catches what was recorded: *a note may cite
nothing the model was not shown.* Two checks: a deny-list of evidence this
pipeline does not carry (CVV/AVS, 3-D Secure, reason codes, IP address,
device identity, per-card history, geography, MCC, dispute history), and
every rupee amount and percentage in the note must appear in the prompt
within rounding. Validation lives inside `explain_flag`, so no caller can
obtain an unvalidated note; a rejection falls back to the template with exit
code 4 and the rejected note logged to stderr for the record.

Run over the committed cassettes (`spandan validate-cassettes`):

| cassette | model | verdict | reasons |
|---|---|---|---|
| `9738bd8f…` (₹5.45 probe) | gemini-3.1-flash-lite | **REJECTED** | cites CVV/CVC result; cites AVS result |
| `7e36f73e…` (₹150 sale FP) | gemini-3.1-flash-lite | **REJECTED** | cites decline reason code; cites per-card history; cites IP address |
| `a39301b4…` (₹5.45 probe, plain prompt) | claude-haiku-4-5 | accepted | — |
| `424749a7…` (₹5.45 probe, grounded prompt) | claude-haiku-4-5 | **REJECTED** | cites AVS result; cites decline reason code |
| `3cf90c49…` (₹150 sale FP, plain prompt) | claude-haiku-4-5 | **REJECTED** | cites merchant category; figure 50% not in the evidence |

**4 of 5 rejected.** The two Gemini notes each on the exact fabrication the
reading found. The deterministic template passes the same validator by
construction (`test_validator_accepts_the_template`), which is the property
that makes it the fallback. The Haiku notes, recorded 2026-09-03 through the
Anthropic API and kept exactly as returned, are a different finding each:

- **Plain prompt, accepted.** The first recorded note to survive the validator:
  every amount and percentage is in the prompt and no field outside it is
  named. Its judgement is uneven — it calls the ₹5.45 amount "opposite of
  fraud scaling behavior" and two sentences later says card testing uses low
  amounts — and its next action ("check merchant mer_008's decline logs",
  "request cardholder confirmation") has no decision rule tied to the
  evidence, where the template's does. Grounded, and no sharper than the
  template on this case.
- **Grounded prompt, rejected — and not for invented evidence.** Told that the
  decline code and AVS result do not exist, the model made obtaining them its
  next action and conditioned the release on them ("Request decline reason
  code from processor. If the decline was … AVS mismatch … release the
  card"). It does not claim to hold those fields; it conditions the action on
  data it was told is unavailable, which the grounding rule forbids in so many
  words and the deny-list catches by mention. It also calls the BIN's baseline
  ticket "this card's baseline" — a card-level claim this pipeline cannot
  make — and the deny-list did **not** catch that. One sample; whether
  enumerating the missing fields primed the model to reach for them is a
  question this table cannot answer yet.
- **₹150 sale, plain prompt, rejected.** Two reaches: "check merchant
  category", a field the pipeline does not carry, and a decision rule built on
  a 50% decline threshold that appears nowhere in the evidence — the validator
  treats every percentage as a claim about the evidence, and an invented
  threshold is caught by the same rule as an invented measurement. More telling
  than the rejection: this is the ambiguous case where `TARGET.md` argued a
  model note could beat the template by reading ₹150 as a price point and
  leading with "check for a sale first". Over the wire it did not: it read
  ₹150 as an "atypical low amount" and dismissed the flag as n=1 noise.

What ships is therefore: the model's note **if** it cites nothing outside its
prompt, the template otherwise. On the five recorded notes that means the
template four times and the model once. What the validator cannot do, stated
so nobody over-reads it: it cannot tell a wrong inference from a right one — a
note that reasons badly from real evidence passes, and the accepted Haiku note
shows what that looks like; it cannot tell citing a field from recommending its
retrieval, because it works by mention; and it cannot catch a mis-attribution
that uses allowed words, as "this card's baseline" did. It catches the failure
class that was actually observed on the Gemini notes, invented evidence, and
nothing more.

The second prompt variant (`render_prompt(flag, grounded=True)`) adds an explicit
enumeration of what this pipeline does not have. On the one flag recorded with
it so far, telling the model did not stop it reaching for those fields; the
₹150 grounded-prompt note is still to be recorded and goes in this table when
it exists. Until then the claim is only what the table shows: the validator
catches what was recorded, one note in five was grounded, and the grounded
prompt did not prevent the reach it was written to prevent.

## 9. Learned weights versus hand weights

The design claim is that six hand-weighted terms, summed, are enough. The
fair test is a model that learns its own weights over the same terms, reported
through the same pipeline, and that is what `make baselines` does.

**Method.** `spandan.eval.features` pushes every event through
`ReferenceDetector._advance`, the per-event step the detector itself uses, and
reads the six term values out of the evidence dict it already builds; the
detector score it records is asserted identical to the harness pass, bit for
bit, on all 805,066 test events. Three event columns are added for the richer
model: log-amount, declined, hour of day. Two models fit on the warm-up window,
the first 75% of the training days (599,309 events), which is the only
labelled data that is neither the validation window nor the test window. Each
threshold is chosen on validation under the same alerts/day ≤ 10 budget as the
detector, and each model is read once on test. Three seeds, as in §0. Nothing
here is shipped: the detector is frozen, `spandan.detect` imports neither this
module nor scikit-learn, and a test asserts both.

**On the stream every other figure comes from** (seed 20260824; learned
thresholds are log-odds):

| model | features | threshold | precision | alert precision | precision @ 0.15% | recall | PR-AUC | net ₹ | alerts/day (test) | legitimate declined |
|---|---|---|---|---|---|---|---|---|---|---|
| hand | six terms, hand weights | 21.99 | 0.4462 | 0.433 | **0.0824** | 0.8444 | 0.6615 | 279,151 | 9.7 | 1 in 71 |
| logreg6 | the same six terms, learned weights | -1.498 | 0.5333 | 0.413 | **0.1130** | 0.9113 | 0.8541 | 619,275 | 10.0 | 1 in 93 |
| gbm9 | six terms + log-amount, declined, hour | -5.432 | 0.4525 | 0.330 | **0.0844** | 0.9599 | 0.6857 | 714,187 | 10.6 | 1 in 64 |

Negative controls, events flagged (false positives by construction):

| model | flash_sale | issuer_outage | outage_single_merchant |
|---|---|---|---|
| hand | 20/17,787 (0.1%) | 1,818/18,057 (10.1%) | 9,170/18,169 (50.5%) |
| logreg6 | 0/17,787 (0.0%) | 72/18,057 (0.4%) | 8,199/18,169 (45.1%) |
| gbm9 | 0/17,787 (0.0%) | 18/18,057 (0.1%) | 12,091/18,169 (66.5%) |

Attack scenarios, share of events flagged:

| model | burst | rotating | slow_low |
|---|---|---|---|
| hand | 0.9619 | 0.8252 | 0.4446 |
| logreg6 | 0.9640 | 0.9079 | 0.7105 |
| gbm9 | 0.9817 | 0.9317 | 0.9904 |

What the linear model learned, as a multiplier on the hand weight already
inside each term (1.0 would mean the hand weight was right):

| term | hand weight | learned multiplier |
|---|---|---|
| velocity_bin | 1.0 | +0.122 |
| decline_bin | 2.0 | +0.126 |
| amount | 1.0 | +1.618 |
| velocity_ip | 0.5 | -4.538 |
| repetition (damping) | -1.2 | +5.053 |
| merchant_span (damping) | -1.4 | -0.093 |

**Across three independently generated streams** (median, min–max):

| model | precision @ 0.15% | recall | net ₹ |
|---|---|---|---|
| hand | 0.0824 (0.0672–0.1185) | 0.8444 (0.8189–0.9106) | 348,845 (279,151–395,007) |
| logreg6 | 0.0845 (0.0728–0.1130) | 0.9228 (0.9113–0.9570) | 632,567 (619,275–671,464) |
| gbm9 | 0.0696 (0.0591–0.0844) | 0.9680 (0.9599–0.9790) | 704,784 (702,460–714,187) |

**Environment note.** Regenerated in three fresh environments — a second clone
on the same Windows machine, WSL Ubuntu (glibc), and the CI runner — every
figure above, all three rows, is identical at the quoted precision on every
seed, multipliers included, with scikit-learn at the pinned 1.9.0. The
features themselves are bit-identical across those environments: the 1e-14
drift in the reference's scores across C runtimes (BUILD_LOG, 2026-09-03,
entry 13) does not reach a single baseline figure. What did move a figure was
the library version. The first run of this section happened to use scikit-learn
1.8.0, under which the boosted model fit different trees on the same features:
precision 0.4427, recall 0.9651, precision at 0.15% 0.0814, 12,644 of the
outage flagged (69.6%), three-seed medians 0.0699 / 0.9651 / 705,752. Those
figures stood here until 2026-09-03 and are **superseded** by the pinned run;
BUILD_LOG entry 15 records that entry 14 attributed the difference to the C
runtime and was wrong. Nothing the boosted row says changed between versions:
lowest of the three at the realistic base rate, highest on recall, hardest on
the outage. It is the one model whose result depends on its library, because
histogram binning and greedy splits amplify implementation changes that a
weighted sum does not see, and that is why the version is pinned rather than
the figure tolerated. `make check` demands every row exactly.

**Reading it.**

1. **Precision at the realistic base rate does not move.** The linear model
   sits at a median 0.0845 against the hand weights at 0.0824, each inside the
   other's range; the boosted model is lower at 0.0696. The 0.1130 on the base
   stream is one seed. Whatever is limiting precision at a 0.15% base rate, it
   is not the choice of weights over these terms.
2. **Recall does move, at the same alert budget.** The linear model's worst
   seed (0.9113) is above the hand weights' best (0.9106); the median gain is
   eight points, and it lands where the detector is weakest: slow_low 0.44 →
   0.71, rotating 0.83 → 0.91. The modelled net roughly doubles, which is
   mostly that recall priced through the rupee model, with the §6 caveat that
   the model under-prices false positives still standing.
3. **Neither learned model fixes the measured failure.** The single-merchant
   outage goes from 50.5% flagged to 45.1% under the linear model, which
   up-weights the repetition damping five-fold and still cannot separate it,
   and to 66.5% under the boosted model, which buys its recall partly by
   flagging the outage harder. The issuer-wide outage, by contrast, nearly
   vanishes (1,818 → 72 → 18 events). The single-merchant outage is separable
   from an attack only by a signal these nine features do not carry, which is
   what §2.1a's kill-switch supplies from the trailing hour rather than the
   five-minute window.
4. **What the learned weights say about the hand weights.** By the linear
   model's account the two headline terms, BIN velocity and BIN decline
   excess, are weighted about eight times too high (×0.12 each); the amount
   term is under-weighted (×1.6); repetition damping is under-weighted
   five-fold; merchant-span damping does almost nothing (×−0.09); and the
   per-IP velocity term has the wrong sign (×−4.5): in this traffic, holding
   the other terms fixed, high per-IP velocity argues against card testing.
   That is a real criticism of the hand weights. It comes with the caveat that
   the model was fitted in-distribution, on the warm-up window of the same
   generator, which the hand weights were written before ever seeing.

**What it means for the freeze.** The linear model is the change that would
ship next if recall at the same alert budget is what the merchant wants, and
this section is the evidence that decision would be made on. It is not shipped;
the detector stays frozen and every headline figure is still the hand-weighted
one. The next detector-level change that would move the number this project
leads with is not a re-weighting. It is a feature.
