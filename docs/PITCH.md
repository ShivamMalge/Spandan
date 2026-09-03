# The five-minute pitch

Structure, not a script. Every number below is one that `make eval`,
`make baselines` or `make experiment` prints and `make check` verifies against
`data/metrics.json`, `data/baselines.json` and `data/experiment_*.json`. Nothing
here is quoted that the checker does not cover. Timings are a budget; the demo
recording in the README covers minute 2 if the terminal is not live.

---

## 0:00 — The loss, in one breath (30 s)

Stolen card numbers are worthless until someone knows which ones still work,
and the cheapest way to find out is a merchant's checkout: a run of tiny
authorizations, mostly declined, on a few issuing BINs, faster than real
customers arrive. No single attempt is anomalous. The rate and the
concentration are. Spandan scores each authorization attempt against
per-entity baselines it learns from the stream, and a flag declines the
transaction.

**Say:** "one class of loss, inline, defense-only, and every figure I show
reproduces from a fresh clone with one command."

## 0:30 — Two numbers, the least flattering first (45 s)

| what | figure | where it prints |
|---|---|---|
| precision at a realistic 0.15% base rate | **0.0824** — eleven false alarms per catch | `make eval`, headline block |
| legitimate transactions declined as an inline control | **1.41%, 1 in 71** | same |
| recall, 60 of 60 attack episodes | 0.8444 | same |

**Say:** "the generator's own positive rate is ten times a realistic
merchant's, so the 0.4462 precision at that rate flatters the detector by an
order of magnitude. I lead with the number that does not."

## 1:15 — What would actually ship (45 s)

Inline blocking is not deployable at any alert budget. The configuration this
project would ship is alert-only at budget 2:

| | figure |
|---|---|
| alerts per day | 2.1 |
| precision at the 0.15% base rate | 0.2034 |
| attack episodes surfaced | 35 of 60 |
| legitimate traffic flagged | 0.37%, **1 in 271** |

**Say:** "one analyst, two items a day, 58% of campaigns surfaced. The caveat
travels with it: with flags notifying rather than declining, nothing is
prevented until a human acts, so the rupee net does not transfer to this
configuration and I do not claim it."

## 2:00 — The demo (60 s, recorded)

`spandan replay --data data --limit 20000`: 221 flags on 20,000 test events,
218 of them real card testing, the exposure counter climbing to ₹8,407. Then
the engine swap: `make eval ENGINE=rust` against `ENGINE=python`, two metrics
files, one differing line, the engine label. Then `spandan explain` on the
₹150 flash-sale false positive: the model's note is rejected by the validator
for naming a field the pipeline does not carry, and the deterministic template
prints instead, exit code 4.

**Say:** "two engines, bit-exact over 1.6 million events; and the language
model is downstream of every number, never upstream."

## 3:00 — The one measured failure, and two measured responses (60 s)

A single-merchant issuer outage: **50.5%** of its events flagged as card
testing, because at a five-minute window an outage's retries and a burst's
distinct cards look alike. Two responses, both measured, neither hidden:

| response | outage flagged | legitimate declined | shipped? |
|---|---|---|---|
| frozen detector | 50.5% | 1 in 71 | yes, this is the headline |
| triage kill-switch, registered on train before the test was read | flags unchanged; **20 trips**, all on the outage, none on an attack | **1 in 102** | yes — routing, not detection |
| long-horizon window, hand weight 1.2 | 35.6% | 1 in 95 | no |
| long-horizon window, weight 6.0 | **18.0%** | 1 in 166 | **no — fails the gate registered before measurement (15%), and costs 4.7 points of recall** |

**Say:** "the fix is built, measured, and not shipped, against a gate I wrote
down before I saw the number. That is the difference between a measured fix
and a story about one."

## 4:00 — Where AI is, and where it is forbidden (45 s)

- The detector, the threshold, the costs, the triage decisions: deterministic.
  A test poisons the `spandan.llm` import and re-runs the evaluation; every
  number survives.
- The explainer: six recorded notes, kept exactly as returned, **5 of 6
  rejected** by a grounding validator for naming evidence the pipeline does
  not have. The one accepted note was no sharper than the template.
- Learned weights on the same six terms: precision at the base rate does not
  move (0.0845 against 0.0824); recall does (0.9228 against 0.8444); neither
  learned model fixes the outage (45% and two in three against 50.5%).
  Reported, not shipped.

**Say:** "the hand weights are wrong by the linear model's account, and the
outage is a feature problem, not a weight problem. Both are in the repo."

## 4:45 — Close (15 s)

130 Python and 33 Rust tests, a figure checker over four hundred documented numbers,
three CI jobs that regenerate the stream and the evaluation from nothing on a
machine that is not mine, and a build log with fifteen entries each written
with the wrong diagnosis before the right one.

**Say:** "`make check` is the pitch. Everything I said is a line it prints."

---

## The questions a judge asks, and the answers

- **Where is Razorpay?** Nowhere in the tree yet, by decision: the detector
  sits upstream of the gateway, on the authorization stream. The next build is
  a `payment.failed` webhook adapter carrying `error_source`, which is also
  the field that would fix the outage failure. Both are named in
  FAILURE_MODES §7 and the external audit.
- **So you would not deploy it?** Not inline, and the README says so in its
  first paragraph. Alert-only at budget 2 is the shippable configuration.
- **Why is precision so low?** Because it is measured at a realistic base
  rate, on a stream with a control built to defeat this detector. Competitors
  quoting 94% precision quote the generator's rate with no control.
- **Why not a dashboard?** A static report generated by `make eval` is the
  right surface for a single-writer, cold-start detector; a live UI belongs
  after persistence and the ingestion contract exist.
- **What broke?** Fifteen things, each in BUILD_LOG with the wrong diagnosis
  first. The most recent: a library version moved a figure and I blamed the
  C runtime for a day.
