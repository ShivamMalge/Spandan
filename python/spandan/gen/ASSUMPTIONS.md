# Generator assumptions

Every metric this project reports is downstream of this file. There is no public
dataset with the BIN, IP and device fields a card-testing detector needs, so the
stream is synthetic — which means the honesty of the evaluation rests on being
explicit about what was assumed and where the synthetic stream is unlike real
traffic.

Two sections: **what was chosen and why**, then **how this differs from real
traffic**. The second section is the more important one. Nothing in it is a
disclaimer added after the fact; each item is a known limitation that should be
read alongside any number the detector produces.

Values referenced here live in `config.py`. The manifest records a SHA-256 of the
full config, so any figure can be traced to the settings that produced it.

---

## 1. What was chosen, and why

### 1.1 Span, volume and the split

| Choice | Value | Why |
|---|---|---|
| Stream span | **100 days** from 2026-06-01 00:00 IST | See below — the span is set by statistical power, not by realism. |
| Train window | days 0–50 | Phase 2 carves its validation window (the last 25%) out of this period. Thresholds are never selected on test. |
| Test window | days 50–100 | Strictly later. The boundary is a wall: no event straddles it, asserted by `test_no_episode_straddles_the_split_boundary`. |
| Merchants | 10 | Enough for per-merchant baselines to differ; small enough that each has real volume. |
| Volume | ~1.61M events | ~804k train / ~805k test. |
| Episodes | **20 per scenario per split**, 240 total | Two was an anecdote. See below. |

**Why the span is 100 days and not 14.** The first version of this stream ran 14
days and put two episodes of each scenario in the test window. Phase 2 measured
what that costs: per-scenario recall swung 0.16–0.54 across three seeds, because
every per-scenario number rested on a sample of two.

The fix had to raise statistical power without making the task easier or harder.
So the **episode rate per day is unchanged** (~0.4 per scenario per day) and the
stream was made longer. Packing more episodes into the original 14 days would
have been a difficulty change wearing a statistics costume: more attack traffic
per day means more attack traffic folded into the per-BIN baselines those attacks
are measured against. `test_more_episodes_came_from_a_longer_stream_not_a_denser_one`
asserts the density is unchanged, so this cannot silently drift.

A 100-day stream is also more realistic than a 14-day one for baseline warm-up,
but that is a side benefit and not the reason.

The start timestamp is a fixed constant, never "now". A dataset that changes when
you regenerate it next month is not a held-out test set.

### 1.2 Benign arrivals

Poisson per merchant-hour, with the rate scaled by a diurnal multiplier, then
placed uniformly inside the hour.

- **Diurnal shape**: one raised cosine, trough 0.28× and peak 1.95×, peaking at
  20:30 IST. Evening-peaked Indian consumer traffic.
- **Weekend lift**: flat 1.18× on Saturday and Sunday.
- **Per-merchant base rate**: uniform in 18–72 transactions/hour, so merchants
  differ by roughly 4× in size.

Poisson arrivals mean the *benign* stream already has minute-to-minute variance.
A detector that flags any above-average minute will discover this immediately,
which is the point — the benign baseline has to be able to punish a naive rule.

### 1.3 Benign decline rate

Each merchant draws a fixed benign decline rate, uniform in **4.5%–11.5%**. The
band is declared in the config and asserted in `tests/test_gen.py`.

The number that matters is that it is **nonzero and substantial**. Card testing
is characterised partly by an elevated decline ratio; if benign traffic declined
at ~0%, decline ratio would be a free separator and every reported metric would
be an artifact of that choice. A benign rate in this band means the detector has
to distinguish 85% declines from an ordinary 8%, not from zero.

### 1.4 Amounts

Lognormal per merchant: median drawn in ₹450–₹3,200, log-sigma in 0.55–1.05,
clipped to ₹10–₹5,000. Lognormal because basket sizes are multiplicative and
right-skewed. The clip is a modelling convenience, not an observed bound.

### 1.5 Entity reuse

Cards, IPs and devices are drawn from fixed pools with **Zipf(1.35)** popularity
weights — frequent customers are common, one-time customers form the tail. Chosen
over an explicit "repeat rate" parameter because reuse then falls out of one
interpretable exponent instead of a hand-tuned fraction.

BIN popularity uses a much flatter **Zipf(0.65)**: a few issuers carry most
volume, but issuer share is not as skewed as individual customer activity.

Pools: 9,000 cards, 6,500 IPs, 5,200 devices, 220 BINs.

### 1.6 Synthetic identifier ranges

Not "made up" — drawn from ranges reserved by standard, so the claim that nothing
collides with anything real is checkable rather than asserted (`agents.md` §7).

| Field | Range | Standard |
|---|---|---|
| BIN | leading digit `0` | ISO/IEC 7812 MII 0 is reserved to ISO/TC 68 and is not issued to card schemes; every live scheme is at 1–6. |
| IP | 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24 | RFC 5737 TEST-NET-1/2/3 |
| IP | 198.18.0.0/15 | RFC 2544 benchmarking range |
| Card | `card_` + 10 digits | Opaque token. **Not a PAN**, not derived from one, not Luhn-valid, and never 13–19 digits. No card number is generated anywhere in this project. |
| Device | `dev_` + 10 digits | Opaque token. |

`test_all_identifiers_synthetic` and `test_no_card_reference_could_be_mistaken_for_a_pan`
check every event, not a sample.

### 1.7 The six scenarios

Described as statistical signatures — rate, entity concentration, amount band,
decline ratio — and nothing else (`agents.md` §7). Each appears in both the train
and the test window, because Phase 2 needs positives in the training period to
select a threshold on a validation window.

| Scenario | Label | Signature |
|---|---|---|
| `burst` | 1 | One BIN, one IP, one device, 190–300 distinct cards, ₹1–₹60 band, decline ratio 0.83–0.89, 9–15 minutes. |
| `rotating` | 1 | Same BIN and card concentration; IP and device spread across 55–74 values, 22–31 minutes. No single address carries unusual volume. |
| `slow_low` | 1 | Same concentration, 48–66 events spread over 5.5–7 hours, decline ratio 0.69–0.75. Sits underneath a fixed per-window count. |
| `flash_sale` | **0** | 760–1,020 distinct cards over 45–60 minutes, many issuers, ordinary amounts, decline ratio 0.14–0.17. Negative control on the **volume** axis. |
| `issuer_outage` | **0** | One BIN, 55–78 minutes, 760–1,050 events across only ~30% as many cards (retries), ordinary amounts, decline ratio 0.79–0.86, spanning 4–5 merchants. Negative control on the **decline-ratio** axis — see §1.7b. |
| `outage_single_merchant` | **0** | The same outage at **one** merchant, with amounts in the same low band as a probe burst. Strips away the two separators the detector was actually using, leaving only retry structure. The hardest control by a wide margin — see §1.7c. |

Two modelling choices inside these matter more than the numbers:

**Attack episodes borrow a BIN that also carries benign traffic.** The BIN is
drawn from the benign pool rather than invented. This is both more realistic — an
issuer whose cards are being tested still has ordinary customers — and strictly
harder, because the BIN has a legitimate baseline instead of appearing from
nowhere. Asserted by `test_attack_bins_also_appear_in_benign_traffic`.

**The flash sale is a mixture of known and new customers.** The configured
parameter draws 75% of its entities from the benign pools with the *same*
popularity weights as benign traffic, and 25% as first-time customers with unseen
cards, IPs and devices.

The **realised** known-customer share on the shipped dataset is **57.5%**, not
75%, because a weighted draw over a 9,000-card pool still selects some cards that
never actually transact in a 14-day window. The realised figure is the one that
matters, so it is measured on every build and recorded in `manifest.json` under
`negative_case` rather than being inferred from the parameter. The generator was
not re-tuned to hit the configured number — 57.5% known / 42.5% new is a genuine
mixture, and adjusting the pool size to make the parameter come out "right" would
be fitting the data to a figure invented here.

This is the single most load-bearing choice in the file:

- All-known customers would make "many unseen cards" a free separator between
  sales and attacks.
- All-new customers would make it a free separator in the other direction.
- Drawing the known share *uniformly* over a Zipf-shaped pool has the same effect
  as the second case, because a uniform draw mostly selects cards that never
  actually transacted. This was a real bug in the first version of the generator,
  caught by a test that measured the overlap rather than assuming it. See
  `BUILD_LOG.md`.

So novelty alone decides nothing, and the detector has to earn its separation from
velocity, concentration and decline structure.

### 1.7a BINDING CONSTRAINT — no card-novelty feature, in any phase

This is a standing constraint on the detector's design, not a note.

The two populations differ in card novelty:

| Population | Cards never seen elsewhere in the stream |
|---|---|
| Attack scenarios (`burst`, `rotating`, `slow_low`) | **100%** — no attack card appears in benign traffic |
| `flash_sale` | ~42% |
| `issuer_outage`, `outage_single_merchant` | ~10% |

So the flash sale controls fully for **volume** and only **partially** for
**novelty**. A detector that keyed on "share of never-before-seen cards" would
separate attacks from sales partly for free, and the reported precision would be
measuring an artifact of the generator rather than a property of the detector.

**That is acceptable only because no card-novelty feature exists anywhere in the
design.** The five Rust modules key on BIN, IP, device and merchant velocity, and
on decline ratio. None of them tracks whether a card has been seen before.

Therefore, binding on Phase 2 and Phase 3:

> **No feature may be derived from card novelty, first-seen-ness, or distinct-card
> counts used as a proxy for novelty.** If such a feature is ever added, the flash
> sale immediately ceases to be a valid negative control, and the negative case
> has to be rebuilt before any metric is reported.

`tests/test_gen.py::test_no_card_novelty_feature_exists_anywhere` enforces this by
scanning the detector packages, so the constraint fails a test rather than relying
on anyone remembering it.

### 1.7b The issuer-outage negative control

The second labeled-clean scenario, and the harder of the two, because it attacks
the detector's **primary** axis rather than a secondary one.

An issuer outage produces an elevated decline ratio concentrated on a single BIN
from entirely legitimate traffic. That is, feature for feature, the card-testing
signature — with no attacker present. It is common in real payments, and it is the
first false-positive case a risk panel raises.

Measured on the shipped stream:

| Property | issuer_outage | burst | What it means |
|---|---|---|---|
| Decline ratio | **82.4%** | 83–89% | Indistinguishable on the primary signal |
| Distinct BINs | 4 (one per episode) | 1 per episode | Equally concentrated |
| Attempts per card | **5.36** | 1.58 | Customers retry a declined payment; a probe does not revisit a dead card |
| Median amount | **₹1,664** | ₹26 | Ordinary basket, not a low probe band |
| Known-customer share | **89.9%** | 0% | Existing customers with existing baselines |
| Merchants spanned | 4–5 per episode | 1 | An issuer's customers shop in more than one place |

The four rows in bold are the separators available to the detector, and **none of
them is decline ratio**. This makes the control hard but learnable: if the outage
were separable only by decline ratio it would be genuinely indistinguishable from
card testing, and including it would be setting an impossible bar rather than a
demanding one.

Volume is elevated during an outage because customers retry — which is why this
is a genuine test of the velocity features too, not only of the decline features.

Its false-positive count and rupee cost are reported separately from the flash
sale's, because they are different failure modes with different operational
responses: a flash sale is a merchant event, an outage is an issuer event.

`test_issuer_outage_attacks_the_primary_signal` and
`test_issuer_outage_is_separable_from_a_burst_without_decline_ratio` assert both
halves — that it is attack-like on the primary axis, and that the separators
actually exist.

### 1.7c The single-merchant outage: the control that removed the crutch

Phase 2 measured *which* separator was doing the work in §1.7b, and the answer was
uncomfortable: the multi-merchant outage was being rejected by **merchant span and
amount**, not by the retry structure it was built around. The retry signal is
largely invisible to the detector, because retries are spread across a 60-minute
episode while the detector's repetition term looks inside a 5-minute window — and
within any single window an outage's cards look nearly as distinct as a burst's.

So the control was passing for reasons that had little to do with the axis it was
built to test. `outage_single_merchant` removes both crutches:

| | `issuer_outage` | `outage_single_merchant` | `burst` |
|---|---|---|---|
| merchants | 4–5 | **1** | 1 |
| amounts | ordinary baskets | **₹1–₹60 band** | ₹1–₹60 band |
| retries per card | ~3.3 | ~4.7 | ~1.0 |
| decline ratio | 0.79–0.86 | 0.79–0.86 | 0.83–0.89 |

What is left to separate it from card testing is retry structure and nothing else.
This is also the outage a payments panel pictures — a single merchant seeing a wall
of small declines on one BIN — so it is the realistic version as well as the hard
one.

It fires. See `docs/FAILURE_MODES.md`: this is now the largest measured failure
mode in the project, and it is a result rather than a defect in the control.

### 1.8 Determinism

One `SeedSequence` spawns independent streams for pools, merchants, benign
traffic, and **one per episode**. Independent per episode so that retuning one
scenario does not shift the draws of every other — otherwise every edit silently
rewrites the whole dataset and no before/after comparison means anything.

Output is gzipped JSONL with `mtime=0`, because gzip otherwise stamps the current
time into the header and two identical runs would differ. Same seed, same bytes;
`test_seed_reproducible_byte_identical` checks it.

---

## 2. How this is unlike real traffic

Read this section before believing any number from `make eval`.

**2.1 One diurnal shape for every merchant.** Real merchants have different
rhythms — a food business peaks at meal times, a utility biller at month end.
Here every merchant shares one evening-peaked curve and differs only in scale.
This makes the benign baseline more predictable than reality, which likely makes
the detector look **better** than it would on real traffic.

**2.2 No trend, no seasonality, no holidays.** Volume is stationary across the 14
days apart from the diurnal and weekend terms. Real streams have paydays, sale
seasons and campaign spikes. A baseline estimator that would drift on real data
has nothing here to drift against.

**2.3 Benign decline rates are stationary apart from the modelled outages.** Each
merchant's *baseline* decline rate is fixed for the whole stream. The one modelled
departure is the `issuer_outage` scenario (§1.7b), which is generated, labeled
clean, and costed — so the most common real false-positive cause for this class of
detector is measured rather than disclosed.

What remains unmodelled is the milder end of the same phenomenon: gateway
incidents that lift decline rates by a few points for minutes at a time, and
acquirer-side routing changes. Those are frequent and small, where the modelled
outages are rarer and large. A detector tuned against only the large case may
still over-flag the small one.

**2.4 Independent arrivals.** Poisson means no bursts within benign traffic beyond
Poisson variance — no viral moments, no retry storms after a network blip, no
checkout-abandonment-and-retry patterns. Real benign traffic is burstier than
this, so the flash sale is the *only* benign surge the detector is tested against.

**2.5 Cards, IPs and devices are drawn independently.** In reality these
correlate: a household shares a device, a corporate NAT shares an IP across many
cards, a mobile user's IP changes between transactions. The absence of shared-IP
benign structure removes a genuine source of false positives from the per-IP
features.

**2.6 No geography, no MCC, no 3DS, no issuer identity, no decline reason codes.**
A real risk system uses all of these. The schema is deliberately narrow, so the
detector is being evaluated on velocity and concentration signal alone.

**2.7 The positive rate is high.** ~1.2% of train and ~1.6% of test events are
labeled attacks. Published card-testing rates at a merchant are typically far
lower and far more concentrated in time. A higher positive rate makes precision
easier to achieve than it would be in production. **Any precision figure here
should be read as an upper bound.**

**2.8 Attack episodes are single-merchant.** Each attack episode targets one
merchant. Real campaigns sweep many merchants on the same acquirer simultaneously;
the cross-merchant correlation that would make such a campaign easier to spot is
absent here, which makes this stream harder on that axis. Note the asymmetry this
creates with §1.7b: outages span merchants and attacks do not, so merchant span is
a stronger separator here than it would be against a real multi-merchant campaign.
A detector that leans hard on merchant span will look better here than it should,
and that belongs in the failure-modes section.

**2.9 Labels are perfect.** Every event's ground truth is known exactly, because
the generator wrote it. Real labels arrive late, incomplete and partly wrong —
chargebacks land weeks later, and a declined probe may never be labeled at all.
No label noise is modelled, so measured recall is optimistic relative to what a
production system could ever demonstrate.

**2.10 The decline ratio inside an episode is constant.** Real testing activity
shows a decline ratio that moves as the batch of cards is worked through. Here it
is a fixed Bernoulli parameter for the episode.

---

## 3. What would change these assumptions

If any of the following became available, the corresponding assumption should be
revisited rather than defended: an aggregate benign decline-rate distribution from
a real acquirer (2.3), a real merchant-level diurnal profile (2.1), or a published
distribution of card-testing episode sizes and durations (1.7). None was available
at build time, and no public fraud dataset carries the BIN/IP/device fields this
detector requires — which is why the stream is synthetic in the first place.
