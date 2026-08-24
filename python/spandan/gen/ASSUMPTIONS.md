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
| Stream span | 14 days from 2026-06-01 00:00 IST | Long enough for per-entity baselines (EWMA, Welford) to warm up before the test window opens, short enough to regenerate in ~11s. |
| Train window | days 0–10 | Phase 2 carves its validation window out of this period. Thresholds are never selected on test. |
| Test window | days 10–14 | Strictly later. The boundary is a wall: no event straddles it. |
| Merchants | 10 | Enough for per-merchant baselines to differ; small enough that each has real volume. |
| Volume | ~214k events | ~150k train / ~64k test. |

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

### 1.7 The four scenarios

Described as statistical signatures — rate, entity concentration, amount band,
decline ratio — and nothing else (`agents.md` §7). Each appears in both the train
and the test window, because Phase 2 needs positives in the training period to
select a threshold on a validation window.

| Scenario | Label | Signature |
|---|---|---|
| `burst` | 1 | One BIN, one IP, one device, 190–300 distinct cards, ₹1–₹60 band, decline ratio 0.83–0.89, 9–15 minutes. |
| `rotating` | 1 | Same BIN and card concentration; IP and device spread across 55–74 values, 22–31 minutes. No single address carries unusual volume. |
| `slow_low` | 1 | Same concentration, 48–66 events spread over 5.5–7 hours, decline ratio 0.69–0.75. Sits underneath a fixed per-window count. |
| `flash_sale` | **0** | 760–1,020 distinct cards over 45–60 minutes, many issuers, ordinary amounts, decline ratio 0.14–0.17. |

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

**2.3 Stationary benign decline rates.** Each merchant's decline rate is fixed for
the whole stream. In reality, issuer outages and gateway incidents cause decline
rates to move sharply for tens of minutes — one of the most common causes of a
real false positive for exactly this kind of detector. **This stream contains no
issuer-outage scenario**, so the evaluation cannot measure that failure mode. It
belongs in the failure-modes section as a known blind spot, not as a result.

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

**2.8 Attack episodes are single-merchant.** Each episode targets one merchant.
Real campaigns sweep many merchants on the same acquirer simultaneously; the
cross-merchant correlation that would make such a campaign easier to spot is
absent here, which makes this stream harder on that axis.

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
