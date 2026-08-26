# Interview preparation — Spandan

A complete Q&A for a panel walkthrough with payments-risk and ML people, plus the
fundamentals check a technical interviewer runs on a student.

**How to use this.** Every project answer cites the artifact — file path, commit,
or the exact number — because an answer that could be about any fraud project is a
failed answer. Where the honest answer is unflattering, the unflattering version is
written. Where the repo does not have the number, the question is marked
**UNANSWERED** rather than filled with something plausible; those gaps are the most
useful pages here, and §13 ranks the ten most exposed.

Answers are sized to be said out loud in 60–90 seconds. Lead with the direct
answer; the evidence follows it.

> ### ⚠ READ THIS BEFORE THE PANEL: a documentation discrepancy found while writing this
>
> Writing this file meant checking every cited figure against the **actual output of
> the fresh-clone run**, and that surfaced a real problem: **`FAILURE_MODES.md` §2.1
> and §6 carry figures from an older run** (the 60-point grid, threshold 23.05) and
> were never refreshed when the 600-point grid moved the operating point to 21.99.
> §0.1, §1 and the headline **are** current; the per-scenario and cost-breakdown
> tables are not.
>
> **This document uses the current, verified numbers.** Where they differ:
>
> | figure | FAILURE_MODES §2.1/§6 says | current build produces |
> |---|---|---|
> | `outage_single_merchant` flagged | 7,240 / 18,169 = **39.9%** | 9,170 / 18,169 = **50.5%** |
> | its blocked-good cost | ₹8,595 | ₹11,077 |
> | `issuer_outage` flagged | 1,573 = 8.71% | 1,818 = **10.1%** |
> | `flash_sale` flagged | 4 events | **20** events |
> | `benign` flagged | 84 | **208** |
> | headroom | −250% (threshold 23.05) | **−267.4%** (threshold 21.99) |
> | blocked clean transactions | 13,947, of which 11,800 declining anyway | **11,216**, of which **9,397** |
> | `slow_low` event recall (§1) | 0.267 | **0.445** |
> | flash-sale realised known share (ASSUMPTIONS §1.7) | 57.5% | **43.7%** known / 56.3% new |
>
> **Every correction moves in the unflattering direction.** The worst failure is worse
> than documented — 50.5%, not 39.9%.
>
> Two things follow. **Fix the source documents before the panel reads them**, because
> a reviewer who opens both files finds two different numbers for the headline failure
> and has no way to know which is current. And if asked about it, the honest answer is
> the right one: *this is the plausible-number pattern again, in my own documentation —
> figures that were true when written, left standing after the run that produced them
> was superseded. It was caught by re-deriving every citation from the actual output
> rather than from the document, which is the same mechanism as every other instance.*

---

# 1. DOMAIN FUNDAMENTALS

**Q1. What is card testing?**

Someone holds a batch of stolen card numbers and does not know which are still
live — most are cancelled, expired, or already blocked. Before those numbers are
worth anything they have to be sorted. The cheapest way to ask an issuer "is this
card alive?" is to attempt a small authorization and read the response. The money
in that transaction was never the point; the **answer** was.

What a merchant sees is a rapid trickle of low-value attempts, mostly declined,
concentrated on a small number of issuing BINs, arriving far faster than that
merchant's ordinary customers arrive. Every individual attempt is unremarkable —
the shape is the tell. That is why this is a velocity problem and not a
per-transaction-features problem.

Spandan's three attack scenarios are three shapes of exactly this, described in
`gen/ASSUMPTIONS.md` §1.7 as statistical signatures only: `burst` (one BIN, one IP,
one device, 190–300 cards, ₹1–₹60, 9–15 minutes), `rotating` (same concentration,
but IP and device spread over 55–74 values), and `slow_low` (48–66 events over
5.5–7 hours, sitting deliberately underneath a fixed per-window count).

**Q2. Why does a merchant pay to stop it? The individual amounts are trivial.**

Four separate costs, only one of which is the probe amount.

First, **authorization abuse**: acquirers and schemes penalise merchants whose
decline rates and auth-to-capture ratios go out of band, and a sustained testing
run wrecks both. Second, **the downstream fraud**: cards confirmed live get spent,
and the merchant who unknowingly served as the oracle often carries part of the
consequence. Third, **chargeback ratios**: breaching scheme monitoring thresholds
moves a merchant into remediation programmes with fees and mandated fixes. Fourth,
**infrastructure and reputation** — a testing run is a traffic spike of pure
garbage.

Spandan's cost model (`eval/costs.toml`) prices only two of these — avoided
chargeback exposure and saved authorization fees — and says so. The headline net of
₹348,845 rests on a ₹500 dispute fee and an assumed 0.8 chargeback rate on approved
fraud, both labelled ASSUMPTION in the file, and the scheme-programme and
reputation costs are not modelled at all.

**Q3. What is velocity abuse?**

The class of abuse where no single action is anomalous and only the **rate** is.
One card, one purchase, ₹200 — ordinary. Forty purchases on one card in four
minutes across twelve merchants — not ordinary. You cannot see it by examining any
one transaction, because each one is fine; you can only see it by holding a window
of time and counting.

Card testing is one instance. Others are credential stuffing against a payment
page, refund abuse, promo-code farming, and account-takeover cash-out bursts. The
machinery is the same in all of them: pick an entity to key on, hold a bounded
window, compare the window against what is normal *for that entity*.

In Spandan that machinery is four axes — BIN, IP, device, merchant
(`detect/interface.py`, `AXES`) — each with a 5-minute sliding window
(`window_ms = 300_000`) and its own learned baseline.

**Q4. What is a BIN and what does it tell you?**

The BIN — Bank Identification Number, formally now the IIN — is the leading digits
of a card number and identifies the issuing institution and product. Historically
six digits, migrating to eight under ISO/IEC 7812. From it you can infer issuer,
scheme, card type (credit/debit/prepaid), usually country, often product tier.

Why it matters here: a stolen batch usually comes from a common source, so it
concentrates on a small number of BINs, which makes BIN a natural velocity axis. It
is also the axis that produces this project's worst failure — an issuer outage
produces elevated declines concentrated on **one BIN** from entirely legitimate
traffic, which is feature-for-feature the card-testing signature with no attacker
present.

Spandan uses only the BIN, never a card number. Its synthetic BINs use leading
digit `0` — ISO/IEC 7812 MII 0 is reserved to ISO/TC 68 and issued to no live
scheme — and `test_all_identifiers_synthetic` checks every event rather than a
sample.

**Q5. Authorization, capture, settlement — what is the difference?**

Three separate steps that get collapsed into one far too often.

**Authorization** is the real-time question: the merchant asks the issuer, via
acquirer and scheme, "will you guarantee this amount on this card?" The issuer
approves or declines and, on approval, places a hold on available credit. No money
moves.

**Capture** is the merchant saying "I have delivered — take it," often later:
e-commerce commonly authorizes at checkout and captures at dispatch. An
authorization never captured expires and the hold drops off.

**Settlement** is the actual movement of funds between issuer and acquirer, netted
and batched on a scheme-defined cycle.

This distinction is why card testing lives entirely in the authorization step: the
tester wants the issuer's yes/no and has no interest in capture. It is also why
Spandan is an **inline authorization control** — a flag declines the attempt at
auth time (`detect/interface.py`, "WHAT A FLAG DOES"), because that is the only
moment where the answer can be withheld.

**Q6. What is a decline, and what do decline codes tell you?**

A decline is the issuer refusing the authorization. Broadly two families. **Soft
declines** are conditional — insufficient funds, issuer temporarily unavailable,
retryable "do not honour", 3DS required — and a retry may succeed. **Hard declines**
are terminal — stolen card, closed account, invalid number — and retrying is
pointless and sometimes penalised.

Codes matter because the correct response differs completely: retrying a hard
decline in a loop is itself a signal that you are not a real merchant flow.

**Spandan does not use decline reason codes at all.** The schema carries a binary
approved/declined only (`gen/schema.py`, `ingest.rs::Status`), and `ASSUMPTIONS.md`
§2.6 lists reason codes among the deliberately excluded fields. That is a real
weakness and I would raise it before the panel does: reason codes are probably the
single highest-value field for separating an issuer outage — which throws
issuer-unavailable-family codes — from a testing run, which throws
stolen/invalid-family codes. My worst failure mode is exactly that separation, and
I could not test the fix because I did not model the field.

**Q7. What is a chargeback? Walk the lifecycle.**

A forced reversal initiated by the cardholder through their issuer — not a refund
agreed with the merchant.

The cardholder disputes; the issuer raises a chargeback under a reason code and
provisionally debits the acquirer, who debits the merchant; the merchant may
**represent** — contest with evidence such as delivery confirmation, AVS/CVV
results, 3DS authentication data, usage logs; the issuer accepts or the case
escalates to pre-arbitration and then scheme arbitration, where the loser pays fees
that routinely exceed the disputed amount.

Timelines run to months, which is exactly why production fraud labels are late and
incomplete. `ASSUMPTIONS.md` §2.9 flags this as an optimism in my evaluation:
labels here are perfect and immediate because the generator wrote them, while a
real system trains and evaluates on labels arriving weeks late and partly wrong.
Measured recall of 0.844 should be read as an upper bound for that reason alone.

**Q8. Who actually loses the money in a card-testing scenario?**

It depends on the transaction and the rail, and the honest answer is "it moves."

For **card-not-present** fraud — what this project concerns — the merchant
typically eats it: the issuer charges back, the acquirer debits the merchant, the
merchant loses goods and value. Where 3DS was successfully applied, liability
generally shifts to the issuer, which is one reason 3DS matters commercially and
one more field Spandan does not model.

For **the probe transactions themselves** — tiny, mostly declined — nobody loses
much directly. The loss is the downstream spend on cards the probes confirmed, and
that frequently lands on a different merchant entirely.

That asymmetry is worth saying to a panel: the merchant being used as a
card-testing oracle is providing a free service to someone who will monetise it
somewhere else. Which is precisely why the incentive to block has to be argued in
scheme-ratio and infrastructure terms, not only in direct-loss terms — and why my
own cost model, which prices only direct terms, understates the commercial case.

**Q9. Issuer, acquirer, PSP, merchant — who is who?**

The **issuer** is the cardholder's bank: issues the card, holds the funds, makes
the approve/decline decision, handles disputes for its customer. The **acquirer** is
the merchant's bank: holds the merchant account, receives settlement, carries the
risk if the merchant cannot cover chargebacks. The **scheme** (Visa, Mastercard,
RuPay) is the network and rulebook between them. The **PSP or gateway** — Razorpay,
Stripe, PayU — sits in front of the acquirer providing integration, tokenisation,
routing, retries, and increasingly risk tooling. The **merchant** is the one whose
checkout is being used as the oracle.

Spandan is merchant- or PSP-side software: it consumes an authorization-attempt
stream with BIN, IP, device, merchant, amount and outcome, and returns a score in
time to decline. It is explicitly **not** issuer-side — an issuer sees a
cardholder's whole history across all merchants, which is a far stronger position
and would justify a completely different design.

**Q10. What does Razorpay do, and where would Spandan sit in their stack?**

Razorpay is an Indian payments company: a gateway and PSP letting merchants accept
cards, UPI, netbanking and wallets through one integration, with adjacent products
in payouts, subscriptions and business banking. Because they aggregate many
merchants behind a common integration, they see cross-merchant traffic no single
merchant sees.

Spandan would sit **inline in the authorization path**, between the merchant's
checkout and the acquirer call — scoring an attempt and returning a decline before
the auth request goes out. That placement is forced by the semantics: a flag
declines, and the only moment you can withhold an answer is before it is asked.

Two caveats I would give them unprompted. First, a PSP would want the
cross-merchant view, and my attack episodes are single-merchant by construction
(`ASSUMPTIONS.md` §2.8) — so I have neither tested nor claimed that case. Second,
at 1-in-71 legitimate customers declined, this would not be deployed inline at
their scale as it stands; a first product version would be alert-only, which
invalidates both halves of my rupee model, and `FAILURE_MODES.md` says so.

**Q11. UPI versus card rails — what changes for fraud?**

UPI is India's real-time account-to-account rail: push payments authenticated by
the payer's PIN in their own app, settled instantly, addressed by VPA or QR. Cards
are pull payments — the merchant requests funds from the issuer — settled later
through the scheme.

The fraud shape changes completely. Card fraud is largely **unauthorised-use**
fraud: someone else's credentials, which is why testing stolen numbers is a thing
at all, and why chargebacks exist as the reversal mechanism. UPI has no equivalent
card-testing problem, because there is no static stealable credential to test — the
payer authenticates each push. What dominates instead is **authorised-push-payment
fraud**: social-engineering the genuine account holder into paying voluntarily,
plus collect-request abuse and QR substitution. Recovery is far harder because the
payment was authorised and settlement is instant.

Spandan is a **card-rail detector and nothing else**. It keys on BIN, decline ratio
and authorization velocity — three things UPI either lacks or expresses completely
differently. I claim no transfer of these results to UPI.

**Q12. What is friendly or first-party fraud?**

A genuine cardholder makes a genuine purchase and then disputes it — non-receipt,
non-recognition, unauthorised use — to keep both the goods and the money.
"Friendly" because there is no third-party criminal; the counterparty is the
customer. Some is deliberate, some is genuine confusion (an unrecognised
descriptor, a family member's purchase).

It is a large share of CNP disputes and it is nasty because the transaction looks
perfect at authorization: right cardholder, right device, right address. No
real-time control catches it. Defence is post-hoc — descriptor clarity, delivery
evidence, representment, behavioural history.

**Spandan does not address this and structurally cannot.** It is a velocity
detector operating at authorization, and first-party fraud has no velocity
signature at authorization. Worth naming in a panel to show I know where my tool
ends: card testing and first-party fraud are both "fraud" and share almost no
detection machinery.

**Q13. What are RTO and COD abuse, and are they in scope?**

**COD** is cash-on-delivery, still a large share of Indian e-commerce. **RTO** is
return-to-origin — the parcel comes back undelivered, typically refused or the
customer unreachable. The abuse is ordering on COD with no intent to accept, which
costs the merchant forward and reverse logistics plus tied-up inventory, with no
payment ever attempted. Serious Indian risk stacks model RTO propensity as a
first-class problem.

**Out of scope for Spandan, definitionally**: there is no authorization event to
score. A COD order never touches the card rails, so a detector keying on BIN
velocity and decline ratios has nothing to look at. I mention it because a
Razorpay-adjacent panel may well raise it, and the right answer is a crisp boundary
statement rather than stretching the tool over it.

**Q14. What is a mule account?**

An account used to receive and move criminal proceeds — sometimes rented or sold by
its genuine owner, sometimes opened on stolen or synthetic identity. Detection is a
network problem: you look for accounts whose counterparty graph, funds-in/funds-out
timing and structuring behaviour say "conduit" rather than "customer," and the
strongest signals are cross-account rather than per-transaction.

Not in Spandan's scope. This project scores authorization attempts on card rails
and has no account, no ledger, no counterparty graph, no beneficiary field. I would
say that plainly rather than gesture at the per-entity state as if it were graph
analysis: four axes of sliding-window counters is not a graph, and calling it one
would be exactly the overclaim the rest of the project exists to avoid.

**Q15. What is 3DS and why does it matter here?**

3-D Secure is the card-not-present authentication layer — the issuer challenge, now
largely frictionless risk-based authentication under 3DS2 rather than the old
static-password flow. Two things make it central: it is the mechanism for strong
customer authentication in regulated markets, and successful authentication
generally **shifts fraud liability from merchant to issuer**.

For card testing it is a strong structural defence: a probe that must pass an
issuer challenge is a far more expensive probe.

**Spandan has no 3DS field** (`ASSUMPTIONS.md` §2.6). Worth conceding firmly,
because a payments panel will spot it: in a real stack a large part of the
card-testing answer is authentication and scheme-level controls, not detection. My
honest framing is that Spandan is the detection layer for traffic that has already
arrived at authorization — not a claim that detection is the right primary defence.

**Q16. What is AVS/CVV checking, and why does it appear in this project's failure
section?**

AVS matches the billing address supplied against what the issuer holds; CVV
verification checks the code that is not stored on the stripe or chip. Both are
anti-CNP-fraud checks and both are classic card-testing signals — a tester working
from a numbers-only dump often cannot supply a correct CVV or address.

They appear here for an unflattering reason. **Neither field exists in Spandan's
schema**, and yet the recorded LLM explanation confidently instructed the analyst
to "Block the BIN for 24 hours if the CVV/AVS result on this attempt returned
'Mismatched' or 'Not Supported'" — a decision rule conditioned on data the system
does not have. That is the fabrication finding: `FAILURE_MODES.md` §8,
`BUILD_LOG.md` entry eight, cassette `9738bd8f…` kept exactly as returned.

The domain point underneath: AVS/CVV *would* be good features. Their absence is a
real limitation of the evaluation, and a payments-trained model hallucinating them
is almost evidence of how strongly a reader expects them to be there.

**Q17. What is an authorization rate, and why do merchants care?**

The share of authorization attempts approved. Merchants care intensely because
every decline is potentially a lost sale, and a percentage point of auth rate on
large volume is a large amount of revenue. Whole product teams exist to raise it —
retry logic, network tokens, data quality, issuer relationships.

It is the direct commercial tension with everything Spandan does: **a false
positive here is a self-inflicted decline.** That is why this project reports the
legitimate-decline rate as the deployability number rather than hiding behind the
alert count — 1.41% of legitimate transactions declined, one in 71
(`FAILURE_MODES.md`, headline). CNP false-decline rates in the industry already run
at a few percent, so this detector adds a comparable amount again on top of a
figure merchants already fight hard to reduce.

Saying that in a payments room is the fastest way to establish that I understand
what the tool costs the business.

**Q18. What is a scheme monitoring programme?**

Visa and Mastercard both run programmes tracking merchant chargeback and fraud
ratios against thresholds; breaching them moves a merchant into a remediation stage
with monthly fees, mandated action plans, and at the far end the risk of losing
acceptance. Acquirers watch these closely because they carry the exposure.

Relevance: this is a real part of the answer to "why pay to stop card testing" —
ratio damage can matter more than direct loss. **I would not quote specific
thresholds or fees from memory in a panel**; the correct move is to name the
mechanism and say the numbers are scheme- and version-specific. Spandan's cost
model contains no scheme-programme term at all (`costs.toml` prices auth fees,
chargeback exposure and blocked-good margin only), so ₹348,845 understates the
commercial case in this specific direction.

**Q19. Where does tokenisation fit?**

Tokenisation replaces the card number with a surrogate — a network token, or a
PSP-scoped token — so the merchant stores and transmits something useless if
stolen. It shrinks PCI scope, reduces breach value, and with network tokens tends
to improve authorization rates because credentials stay fresh. It attacks card
testing on the supply side: tokens are merchant- or network-scoped, so a stolen
token is far less portable than a PAN.

Spandan is consistent with a tokenised world by construction — it never sees a card
number. Its card field is an opaque `card_` token used only to count repetition
inside a window, never as an identity across the stream (`ASSUMPTIONS.md` §1.6, and
the binding no-novelty constraint in §1.7a). If anything that makes the design more
deployable, not less: it needs no PAN access to function.

**Q20. If you could add one field to the schema, which and why?**

**Decline reason code**, without hesitation, and the reasoning is a measurement
argument rather than a hunch.

My largest measured failure is `outage_single_merchant`: 50.5% of a legitimate
single-merchant issuer outage flagged as card testing — 9,170 of 18,169 events —
with headroom −267%, the highest-scoring clean event at 80.79 against a threshold
of 21.99 (`FAILURE_MODES.md` §2.1). It fails because the one feature that would
separate them — the same card retried — is invisible at a 5-minute window (§2.2:
1.13 in-window attempts per card for an outage against 1.22 for a burst, so the
damping term actually points the wrong way).

Reason codes attack that directly and cheaply: an outage throws
issuer-unavailable-family codes, a testing run throws invalid/stolen-family codes,
and the separation does not depend on window size at all. Second choice would be a
3DS or AVS/CVV result for the same reason.

I would add that I cannot prove any of this, because I did not model the field. It
is a hypothesis with a mechanism, not a result.
---

# 2. FRAUD DETECTION CONCEPTS

**Q21. Rules, models, or hybrids — what is the difference and what is Spandan?**

**Rules** are hand-written conditions: "decline if more than N declines on one BIN
in five minutes." Cheap, instant, explainable, auditable, changeable in an
afternoon — and brittle, because a human picked every constant and an attacker only
has to step around them.

**Models** learn a decision boundary from labelled data. They find interactions no
human wrote down and adapt when retrained — at the cost of needing labels, drifting
silently, and being much harder to explain to a regulator or an analyst.

**Hybrids** are what production risk stacks actually are: a model score plus a rule
layer for the things you must guarantee (hard blocks, allow-lists, regulatory
constraints, incident overrides).

**Spandan is neither, precisely.** It is a **statistical detector**: six terms
computed from streaming per-entity baselines, summed with fixed weights, compared
to one threshold. Nothing is fit to labels — the weights in `DetectorConfig` are
hand-chosen — so it is not a learned model. But it is not a fixed-constant rule
either, because every term is measured *relative to a baseline the stream teaches
it*: `velocity_bin` is a z-score against that BIN's own EWMA and Welford variance,
so the same absolute event count is unremarkable for a busy BIN and extreme for a
quiet one. The adaptive part is the baseline; the fixed part is the combination.

**Q22. "So where is the machine learning?" — answer that directly.**

There isn't any, and I would rather say so in the first sentence than let it be
discovered.

The decision function is fixed: six terms, fixed weights in `DetectorConfig`
(`w_velocity_bin=1.0`, `w_decline_bin=2.0`, `w_amount=1.0`, `w_velocity_ip=0.5`,
and two damping weights at 1.2 and 1.4), summed and thresholded. Only the threshold
is chosen from data, on a validation window, under a constraint.

Three honest reasons. First, **the hard part here was never the classifier.** It
was the evaluation — temporal splits, base-rate-honest precision, a rupee cost
model, negative controls that attack the detector's own signal — and building that
correctly is what makes any classifier's number believable. Second, **learned
weights would have needed the same evaluation to be trustworthy**, so the evaluation
was the prerequisite either way. Third, **labels are the thing I do not have** in
the real world; a design whose adaptivity lives in unsupervised streaming baselines
rather than in supervised weights degrades more gracefully when labels arrive late,
which §7 of ASSUMPTIONS says they do.

What I will not claim is that a learned model would do worse. **I never built the
comparison** — there is no logistic-regression or gradient-boosted baseline anywhere
in this repo — so "hand-tuned weights are good enough" is an untested assertion, and
it is the single biggest hole in the project. See Q147.

**Q23. Why does class imbalance break accuracy as a metric?**

Because accuracy is dominated by the majority class. If 1.33% of events are attacks
— the observed positive rate in this stream — a model that predicts "never fraud"
scores 98.67% accuracy while catching nothing. Every number that looks impressive
is coming from the negatives.

It gets worse at realistic rates. At the 0.15% prevalence this project assumes as
realistic (`costs.toml [prevalence]`), the always-negative model scores 99.85%. The
metric literally cannot see the problem.

That is why accuracy appears nowhere in this project, and why `RESEARCH.md` names
the accuracy-on-a-SMOTE-balanced-set pattern as the failure mode of the typical
student submission it is trying not to be. The metrics reported are precision at
three different base rates, recall, PR-AUC, and rupees — each answering a question
accuracy cannot.

**Q24. Define precision, recall, and F1, and say when each misleads.**

**Precision** = TP / (TP + FP): of the things I flagged, what share were real.
**Recall** = TP / (TP + FN): of the real things, what share did I catch. **F1** is
their harmonic mean.

Precision misleads when quoted without the base rate, because it moves with
prevalence — see Q25; that is the single most important thing in this project's
headline. Recall misleads when the positives are clustered rather than independent:
here, event-level recall is 0.844 but **episode-level detection is 60/60**, and
those two numbers answer completely different questions. Catching 84% of the events
in every episode is operationally very different from catching 100% of the events
in 84% of episodes, and only one of them is what an analyst cares about.

F1 misleads by pretending precision and recall trade off symmetrically. Here they
do not, at all: a false negative costs a chargeback share, a false positive
declines a paying customer. **F1 does not appear in this project's headline for
that reason** — the rupee model and the constrained operating point replace it,
because they let the asymmetry be priced rather than assumed away.

**Q25. Why does precision move with prevalence, and how did you handle it?**

Because precision depends on how many negatives there are, and recall does not.
Fix the detector — fix its true-positive rate and false-positive rate — and then
shrink the positive class relative to the negative class: every negative is another
chance to produce a false positive, while the positives stay as they are. Precision
falls. Recall is untouched.

That is not a subtlety; it is the difference between a good-looking project and an
honest one. In this stream the positive rate is 1.33% and precision is **0.4462**.
At the 0.15% prevalence `costs.toml` assumes is realistic for a merchant, the same
detector scores **0.0824** — roughly eleven false alarms per true catch. An order of
magnitude, from nothing but the base rate.

The mechanism is implemented in `eval/costs.py::reweight_to_prevalence`: hold the
positives fixed, rescale the negatives to `P × (1−target)/target`, scale FP with
them. `test_reweighting_leaves_recall_unchanged` asserts recall is invariant, which
is the property that proves the reweighting is doing the right thing.

**0.0824 is the headline number of the whole project**, and it leads the README and
`FAILURE_MODES.md`, because the 0.4462 version flatters the detector by ten times.

**Q26. What is PR-AUC, how does it differ from ROC-AUC, and which misleads here?**

**ROC-AUC** plots true-positive rate against false-positive rate. Its problem under
heavy imbalance is that FPR has the number of negatives in its denominator — with
740,349 clean events, thousands of false positives barely move it. ROC curves look
excellent on imbalanced problems almost regardless of usefulness.

**PR-AUC** plots precision against recall. Precision has FP in its denominator
against TP, not against the negative count, so it stays sensitive to exactly the
failure that matters. The baseline for PR-AUC is the positive rate itself, so it
does not flatter.

This project reports **PR-AUC 0.6615** on the headline seed (range 0.6192–0.7928
across three) and computes it in `eval/metrics.py::average_precision`, checked
against a reference implementation by `test_pr_auc_matches_sklearn_reference`.

**ROC-AUC is not computed anywhere in this repo.** That is deliberate on the
reasoning above, but I should state it as a choice rather than pretend the number
exists: if a panellist asks for it, the answer is "not measured, and here is why I
chose the other one" — not a figure.

**Q27. What is calibration, and why does this project not report it?**

A calibrated score means the number is a probability you can act on arithmetically:
of everything scored 0.7, about 70% should be positive. It matters when you feed
the score into an expected-value calculation, or combine it with other models.

**Spandan reports no calibration curve, because its score is not a probability and
asking whether 24.28 "means" anything in probability terms is asking a question the
quantity cannot answer.** The score is a sum of deviation terms — z-scores, a log
amount ratio, damping subtractions — measured in standard-deviation-ish units with
hand-chosen weights. It is monotone-ish evidence, not a likelihood.

This was an explicit scope decision, recorded in `docs/PHASES.md` (Phase 2): the
calibration curve was **dropped and replaced by a cost-versus-threshold sweep**,
because what a merchant needs is not "what is the probability" but "at which
threshold does this stop being worth it in rupees." The sweep answers that; a
reliability diagram would have answered a question nobody asked.

If the project ever grew a probabilistic model, calibration would come straight
back — and I would want it, because the rupee model would then be doing expected
value arithmetic on the score itself.

**Q28. How should a threshold be chosen?**

On data the test set has never touched, against an objective that reflects the
operational constraint — never by looking at test results and picking what scores
best.

Spandan's rule is in `eval/harness.py::select_threshold`: **maximise net rupees
subject to alerts/day ≤ budget**, where the budget is 10/day and lives in
`costs.toml [operations]`, registered before the test window was ever read (commit
`e5b48f8`, 2026-08-24). Ties within 5% of the best net (`NET_EQUIVALENCE_BAND`)
break toward recall. The sweep is 600 points, not 60, because alerts/day is
extremely steep in the threshold and a coarse grid picks a lucky point.

The constraint exists because the unconstrained version was wrong in a way I could
not see until it was pointed out: maximising net rupees alone let the detector buy
recall with false positives, because the same cost model **prices false positives at
almost nothing** — 9,397 of the 11,216 blocked clean transactions were going to
decline anyway, so blocking them costs no margin (`FAILURE_MODES.md` §6). A model that
undervalues false positives was choosing how many false positives to accept. §0.1
records the fix and the before/after.

**Q29. What is alert fatigue, and does your evaluation model it?**

Alert fatigue is the degradation in review quality as alert volume rises: a team
getting 30 alerts a day does not review each one as carefully as a team getting 4,
and past some volume alerts stop being read at all. It is the reason a detector
that is technically better can be operationally worse.

**My evaluation does not model it, and that omission has a consequence I can point
to.** `costs.toml [review]` prices review linearly at ₹40 per alert. At that price
the difference between 27 alerts/day and 4 alerts/day is about ₹900 a day, which is
noise next to the chargeback term — so **the cost model is close to indifferent
between the full detector and its ablations** (`FAILURE_MODES.md` §3.1), and both
ablations beat the full detector on net rupees across 3 of 3 seeds.

That is why I do not let the rupee model cast the deciding vote. I argue for the
full configuration on precision (0.446 versus drop-EWMA's 0.367, with barely
overlapping ranges) and state plainly that it loses on net. Modelling alert fatigue
is recommendation 2 in `FAILURE_MODES.md` §7, and it is unbuilt.

**Q30. Analyst-in-the-loop versus inline blocking — which is this, and why does it
matter so much?**

**Inline blocking. A flag declines the transaction.** That is stated in
`detect/interface.py` under "WHAT A FLAG DOES," and it is the single most
consequential sentence in the project.

It matters because it determines which number decides deployability. If flags only
notified, the alert count would be the operational constraint and the story would
be comfortable: 487 alerts, 9.7 a day, one analyst. Because flags block, the real
constraint is the share of legitimate transactions declined — **1.41%, one in 71** —
and the alert budget bounds only the review queue.

The gap between those two readings is not small: **20,254 flagged events collapse
into 487 alerts** at the headline operating point, 42 events per alert. Capping
alerts looks like capping merchant impact and is nothing of the sort.

This was ambiguous through Phase 2 and the ambiguity was load-bearing — the report
constrained on alerts/day (workload) while the cost model charged blocked value per
event (blocking). Resolving it one way was a review instruction, and it replaced a
flattering number with the real one.

**Q31. What is concept drift and how would it affect this system?**

Drift is the world changing under a fixed model: the relationship between features
and labels moves, so yesterday's boundary is wrong today. Two flavours — covariate
shift (the traffic changes) and true concept drift (what counts as fraud changes,
usually because attackers adapt).

Spandan is partly protected and partly exposed. **Protected**, because the baselines
are streaming and per-entity: EWMA over the last ~30 samples with a Welford variance
means a BIN that gets busier is re-normalised continuously, with no retraining. A
seasonal traffic lift moves the baseline rather than the alarm.

**Exposed**, in two ways. The weights and the threshold are static — a threshold
selected in June is still 21.99 in December, and nothing recomputes it. And a slow
attacker can walk the baseline: because the baseline learns from the traffic it
sees, an episode ramped gently enough over hours becomes normal. `slow_low` is a
deliberately mild version of that and is already my weakest scenario at 0.445
event-level recall.

My generator has **no trend, seasonality or holidays** (`ASSUMPTIONS.md` §2.2), so
drift is essentially untested here. I would present that as unmeasured rather than
handled.

**Q32. What is cold start, and what does it do to this detector?**

Cold start is a detector with no history: every entity is new, so there is no
baseline to deviate from, and any comparison is against nothing.

Spandan's guard is `baseline_min_samples = 20` — an entity scores 0 until its
baseline has 20 samples, because "cold entities are not evidence; they are just
cold." But the failure still shows at the edge of that guard, and it was found the
embarrassing way: the demo disagreed with the evaluation. With empty baselines a
BIN's baseline window count sits near 1.0 with almost no variance, so ordinary
traffic scores **17–21 standard deviations** above it.

Measured (`FAILURE_MODES.md` §2.3): warmed, 230 flagged, 230 truly card testing, 0
false positives. Cold-started, 29 flagged, 24 attack, **5 false positives, all
issuer_outage**.

Two things came out of it. `spandan replay` now warms on the training window like
the evaluation does, and `--cold-start` exists so the failure can be *demonstrated*
rather than described. The mitigation — persisting baselines across restarts — is
recommendation 6 in §7 and is **not built**. A detector deployed against a new
merchant, or restarted, over-flags for its first window of operation.

**Q33. How do systems like Stripe Radar frame these same problems?**

From what is publicly described, the shape is: a large supervised model trained on
network-wide outcomes, refreshed frequently; features that lean heavily on the
network effect — this card, device, or email seen across many merchants; a rules
layer on top that merchants can author themselves; explicit operating points
expressed as risk thresholds; and human review queues as a first-class product
surface rather than an afterthought.

The instructive contrasts with Spandan are three, and I would offer them rather
than wait to be asked. **They have the cross-merchant view and I explicitly do
not** — my attack episodes are single-merchant by construction, and a PSP-scale
system's biggest advantage is precisely the correlation I did not model
(`ASSUMPTIONS.md` §2.8). **They have real labels at scale, arriving late and noisy**,
which is the regime `ASSUMPTIONS.md` §2.9 says my perfect labels do not represent.
And **they treat the review queue as a product**, which is why alert fatigue is a
design constraint for them and an unmodelled term in my cost function.

Where this project holds its own is narrower: the discipline around the evaluation
— temporal split, base-rate-honest precision, negative controls built to attack the
detector's own primary signal — and the fact that the failures are measured and
named rather than absent from the write-up.

**Q34. What negative controls should a fraud evaluation have, and what are yours?**

A negative control is legitimate traffic engineered to look like the thing you are
detecting. Without one, a detector's precision is measured only against ordinary
traffic that never resembled an attack, which is a test it cannot fail informatively.

Spandan has three, each attacking a different axis (`ASSUMPTIONS.md` §1.7):

**`flash_sale`** attacks the **volume** axis — 760–1,020 distinct cards over 45–60
minutes, many issuers, ordinary amounts, decline ratio 0.14–0.17. Result: 4 flagged
events of 17,787. The volume axis is handled.

**`issuer_outage`** attacks the **decline-ratio** axis — one BIN, 82.5% declines
from entirely legitimate traffic, spanning 4–5 merchants. Result: 1,818 of 18,057
flagged, 10.1%.

**`outage_single_merchant`** attacks the same axis **with the crutches removed** —
one merchant, probe-band amounts — after Phase 2 measured that the detector was
rejecting the multi-merchant version on merchant span and amount rather than on the
retry structure the control was built around. Result: **9,170 of 18,169 flagged,
50.5%**, headroom −267%.

The third one is the most valuable thing in the evaluation, and it is the one that
fails.

**Q35. What is the difference between an alert and a flag in your system?**

A **flag** is per-event: this authorization scored above threshold and is declined.
An **alert** is the human-facing grouping — flags deduplicated per (merchant, BIN)
with a 15-minute cooldown (`eval/metrics.py`, `ALERT_COOLDOWN_MS`), so a run of
related declines becomes one item in a queue instead of hundreds.

The ratio between them is the finding. At the headline operating point, **20,254
flagged events collapse into 487 alerts — 42 events per alert.** Which means the
alert budget of 10/day, which sounds like a tight operational constraint, permits a
detector that declines 2.52% of all traffic and 1.41% of legitimate traffic.

That is why `FAILURE_MODES.md` reports flag rate and decline rate beside alerts/day
everywhere either appears, and why recommendation 3 in §7 is to add a joint
event-level flag-rate constraint. **That recommendation is diagnosed and not
implemented**, and the reason is method rather than time: adding a second constraint
after seeing which budgets score well on test is exactly the selecting-on-test this
project spends its credibility avoiding.
---

# 3. STATISTICS AND ALGORITHMS

**Q36. What is an EWMA? Explain it from scratch.**

An exponentially weighted moving average is a running mean that forgets. Each new
sample updates the estimate by a fraction of the gap between the sample and the
current estimate:

    value += alpha * (sample - value)

Unroll it and every past sample is still in there, weighted by `(1−alpha)^k` for
age `k` — geometrically decaying. So it is a weighted mean over all history with
exponentially less weight on older data, computed in **O(1) time and O(1) memory**,
which is what makes it usable per-entity in a streaming detector. A plain moving
average over the last N would need to store N values per entity; multiply by
millions of entities and that is the whole design gone.

Spandan's implementation is `Ewma` in `detect/reference.py`, mirrored operation for
operation in `baseline.rs`. One detail worth stating because it is a real modelling
choice: it decays over **samples, not over wall-clock time**. An entity that
transacts once a month and one that transacts every second decay at the same rate
per observation. Time-decay would arguably be more correct for a payments stream —
that is an unexamined choice rather than a defended one.

**Q37. What does alpha mean, what is half-life, and how do you pick it?**

`alpha` is the weight on the newest sample: 1.0 means "the estimate is the last
value, remember nothing," and 0.0 means "never update." It is unintuitive to choose
directly, so Spandan parameterises by **half-life** and derives alpha:

    alpha = 1 - exp(-ln(2) / halflife_samples)

Half-life is the honest unit: the number of samples after which a shock's influence
has decayed to half. `ewma_halflife_samples = 30.0` in `DetectorConfig`, so alpha
works out to about 0.0228.

How to pick it: it is a bias–variance trade in time. Short half-life = fast
adaptation, noisy baseline, and — the dangerous part for a fraud detector — an
attack can walk the baseline up behind itself. Long half-life = stable baseline,
slow to accept genuine regime change, so a merchant's real growth reads as
anomalous for longer.

**How I actually picked 30 is worth being straight about**: it is a reasoned
default, not a tuned value. There is no half-life sweep in this repo. Given the
sample gate (Q39), 30 samples is roughly 30 minutes of an active entity's history,
which is long relative to the 5-minute window and short relative to the diurnal
cycle. Defensible; not empirically optimised. **UNANSWERED: the sensitivity of any
headline metric to `ewma_halflife_samples` was never measured.**

**Q38. What is Welford's method, and why not just compute the variance directly?**

Welford's is a streaming algorithm for mean and variance in one pass and constant
memory:

    count += 1
    delta = value - mean
    mean += delta / count
    m2 += delta * (value - mean)      # note: the NEW mean

with variance = `m2 / (count − 1)`. The implementation is in `detect/reference.py`
and mirrored in `baseline.rs`.

The naive alternative is to accumulate `sum` and `sum_of_squares` and compute
`E[x²] − E[x]²`. Algebraically identical, numerically dangerous — see Q39.

The two-pass method (compute the mean, then walk the data again summing squared
deviations) is numerically fine but needs the data twice, which a streaming
detector does not have. That is the actual reason: **not accuracy but access.** In a
stream you see each event once.

Spandan tests Welford two ways: `test_welford_matches_two_pass` for correctness
against the batch computation, and `test_welford_survives_a_large_offset_where_naive_variance_fails`
for the numerical property specifically.

**Q39. What is catastrophic cancellation?**

The loss of significant digits when you subtract two nearly equal floating-point
numbers. Each has a fixed relative precision — about 15–16 significant decimal
digits for a double — so if the two agree in the first 12 digits, the difference
retains only the last 3 or 4, and whatever rounding error they already carried is
now the dominant part of the answer.

`E[x²] − E[x]²` is the textbook case. Take values around 10⁹ with a variance of 1:
`E[x²]` and `E[x]²` are both about 10¹⁸ and differ by 1. The subtraction throws away
nearly all the precision, and the result can be wildly wrong — or **negative**,
which is impossible for a variance and is a classic way to crash a square root.

Why it matters here rather than as trivia: Spandan's baselines track per-entity
window counts and amounts. Amounts are in **paise**, so they run in the 10⁵–10⁶
range routinely, and a busy entity's statistics sit far from zero — exactly the
regime where the naive form degrades. Welford's update never forms that difference:
it accumulates `m2` from deviations, which stay small. The comment in `baseline.rs`
says precisely this, and the property test pins it.

**Q40. Sliding windows versus decay — why did you use both?**

They answer different questions and neither substitutes for the other.

The **sliding window** answers "what is happening right now" — exact counts over the
last 5 minutes, with hard edges: `(t−W, t]`, an event exactly W old has fallen out.
It gives an exact, explainable quantity you can put in front of an analyst: "70
events, 68 declined, in five minutes."

The **decay** (EWMA plus Welford) answers "what is normal for this entity" — a
long-run centre and spread that the window is compared against. That is the part
that makes the same absolute count mean different things for a busy BIN and a quiet
one.

Combined: `velocity_bin` is the window count expressed in standard deviations above
the EWMA centre, using the Welford spread (`_velocity_z` in `reference.py`). Window
without baseline is a fixed-constant rule; baseline without window has nothing
current to evaluate. The design needs both, and the ablation that removes the
per-entity baseline — `drop-EWMA` — costs precision, 0.367 against 0.446, with
barely overlapping ranges across three seeds.

**Q41. What is a ring buffer and why is it the right structure here?**

A fixed-size array with head and tail indices that wrap around, so pushing an item
when full overwrites the oldest. Constant memory, O(1) push and pop, no allocation
per event, and excellent cache locality because the storage is contiguous.

It is the right structure here because **memory per entity must be bounded no matter
how hot the entity gets**. A plain list would let one entity under a burst allocate
without limit — which is precisely the traffic pattern an attacker controls. That
is the difference between bounded-memory streaming and fast batch scoring, and it is
the property `test_window_memory_bounded_per_entity` asserts.

`ring_capacity = 512` per entity. When the ring is full the oldest retained event is
dropped and the entity is marked **saturated** — surfaced on the `Flag` as
`window_saturated` rather than hidden, because a score computed from a truncated
window is a score with a caveat and the analyst should see it.

The honest limitation in the same breath: this bounds memory *per entity* and does
nothing about entity count. Entities are never freed, so total memory is linear in
distinct entities — 3,874 bytes each in Rust, projecting 31 GB a month at an assumed
8M entities (`BENCH.md` §4).

**Q42. What is a z-score, and what are its weaknesses here?**

`(value − mean) / standard_deviation` — how many standard deviations from the
centre. It makes quantities with different scales comparable, which is exactly what
you need to sum a BIN's event count with an IP's event count.

Three weaknesses, all live in this project. **It assumes a meaningful mean and
spread**, and window counts are counts — bounded below at zero, right-skewed, more
Poisson than Gaussian — so "3 sigma" does not carry its usual tail probability.
**It is not robust**: mean and variance are both wrecked by the very outliers being
detected, which is why the sample gate matters. And **it degenerates when the spread
is small**: a quiet entity with almost no variance makes the denominator tiny and
every ordinary event enormous. That is not hypothetical — it is exactly the
cold-start failure, where a fresh BIN's baseline count sits near 1.0 with almost no
variance and ordinary traffic scores 17–21 sigma (`FAILURE_MODES.md` §2.3).

The mitigations in the code are `baseline_min_samples = 20` and a `spread <= 1e-9`
guard returning 0.0 in `_velocity_z`. The robust alternative would be a median and
MAD, or a Poisson tail probability instead of a z-score. **Not implemented, not
measured** — a fair criticism, and a cheap experiment I did not run.

**Q43. What would robust statistics change here?**

Median and MAD (median absolute deviation) instead of mean and standard deviation
would make the baseline resistant to the contamination problem: an attack episode
that folds into the baseline it is being measured against pulls a mean and inflates
a variance, but barely moves a median.

The trade is that medians are not O(1) streaming — you need a quantile sketch (P²,
t-digest, or a fixed histogram), which costs memory per entity, which is precisely
the resource this design is trying to bound. At 8M entities a per-entity sketch is a
much worse memory story than 3,874 bytes.

Spandan's answer to contamination is cheaper and cruder: the **sample gate**.
`baseline_sample_interval_ms = 60_000` means an entity's window count folds into its
baseline at most once a minute, and the fold happens **after** scoring, not before.
Without that, a burst would fold hundreds of inflated samples into the baseline it
is being compared against and would partly hide itself. That is a documented
motivation in `DetectorConfig`, not a rationalisation after the fact.

Whether a robust estimator would beat the sample gate is **UNANSWERED — never
measured.**

**Q44. Walk me through the six scoring terms and why they are summed that way.**

Four evidence terms, two damping terms, all in `reference.py::_score`:

- `velocity_bin` = w(1.0) × max(0, z-score of the BIN's window count)
- `decline_bin` = w(2.0) × max(0, window decline ratio − baseline ratio) × 10
- `amount` = w(1.0) × max(0, log(baseline_amount / window_amount))
- `velocity_ip` = w(0.5) × the same z-score on the IP axis
- `repetition` = −w(1.2) × (1/cards_per_event − 1)
- `merchant_span` = −w(1.4) × (distinct_merchants − 1)

The **evidence** terms say "this looks like probing": faster than normal, declining
more than normal, smaller than normal, and concentrated on one address. The
**damping** terms say "this looks like an outage instead": the same card being
retried, and one BIN active at several merchants at once — both things an issuer
incident does and a probe run does not.

Three deliberate shapes. Each term is clamped at zero so evidence can never go
negative — a BIN that is *quieter* than usual should not earn credit. The amount
term is a **log ratio** because being 1/500th of normal and 1/1000th of normal
should not differ by a factor of two in score; multiplicative deviation is the
natural scale for money. And `decline_bin` carries an extra ×10 because a ratio
lives in [0,1] while a z-score does not, so without it the primary signal would be
numerically invisible next to the velocity terms.

The weights are hand-chosen, not fitted. The summation order is fixed and frozen —
`test_the_scoring_terms_are_frozen` — because in floating point the order changes
the last bits, and the Rust core has to reproduce them exactly.

**Q45. Why is `repetition` not a card-novelty feature?**

Because it is computed only from the cards present in the **current window** and
never consults whether a card was seen before. It would behave identically on a
stream where every card had already appeared a thousand times. It measures retry
behaviour, not novelty.

The distinction matters because a card-novelty feature is **banned** in this project
(`ASSUMPTIONS.md` §1.7a, binding). The reason is a data honesty problem: attack
cards in this generator are 100% never-seen-elsewhere, the flash sale is ~42% new,
and outages ~10%. So a detector keying on "share of unseen cards" would separate
attacks from the negative controls partly for free, and the reported precision would
be measuring an artifact of my generator rather than a property of the detector.

The ban is enforced at three strengths: a documented promise, a test that scans the
detector packages (`test_no_card_novelty_feature_exists_anywhere`), and — strongest
— **the Rust type system**: `Axis` has variants for Bin, Ip, Device and Merchant and
no `Card`, so a card-keyed baseline is a compile error rather than a review
oversight.

**Q46. `repetition` is meant to catch outages. Does it work?**

No. It is close to dead weight and possibly harmful, and this is the most useful
negative result in the project.

The design intent was that retries separate an outage from a probe run: an outage
re-attempts the same card, a probe does not revisit a dead card. **Over a whole
episode that holds** — about 4.7 attempts per card for `outage_single_merchant`
against a burst's 1.0.

Inside the detector's 5-minute window it collapses. The outage's retries are spread
over a 55–78 minute episode, so any single window sees mostly distinct cards.
Worked through in `FAILURE_MODES.md` §2.2: about 70 events drawn from about 290
cards in a 5-minute window gives roughly 63 distinct cards — **1.13 attempts per
card** — while a burst packing 240 events into 12 minutes gives about **1.22**. The
damping term is not merely weak; **at this window size it points the wrong way.**

That is the mechanism behind the project's worst number (50.5% of a single-merchant
outage flagged). The fix is a second, much longer window on the BIN axis —
recommendation 1 in §7 — and it is **diagnosed, not built**, because it is a
redesign of the scoring function and the detector was frozen as the parity spec for
the Rust port.

**Q47. What is a property test and how does it differ from a unit test?**

A unit test asserts a specific input produces a specific output. A **property test**
asserts an invariant that must hold for *all* inputs in a generated space, and the
framework generates hundreds of cases and shrinks any failure to a minimal
reproducer.

The difference that matters: a unit test written by the same person who wrote the
code tends to mirror the implementation, so it passes forever including when the
implementation is wrong in the way the author did not think of. A property test
states what must be true independently of how it was done.

This project's best example is not in the detector, it is in the generator.
`test_flash_sale_is_a_mixture_of_known_and_new_customers` asserts a property of the
*output* — that between 55% and 95% of flash-sale cards also appear in benign
traffic — and it **failed at 66%**, exposing a real bug (Q78). A test asserting "the
flash sale draws its cards from the benign pool" would have mirrored the code, passed
forever, and let a broken negative control silently inflate every precision figure
downstream.

The Rust core uses `proptest` for the window and baseline invariants; the Python side
uses generated streams for `window_counts_match_bruteforce` and
`decline_ratio_matches_bruteforce`, which check the incremental aggregates against a
brute-force recomputation.

**Q48. What invariants are worth property-testing in a streaming detector?**

The ones that must hold regardless of input, which is where the bugs live. In this
project:

**Window correctness** — incremental counts must equal a brute-force recount over the
same window (`window_counts_match_bruteforce`, `decline_ratio_matches_bruteforce`).
Incrementally maintained aggregates drift on eviction bugs; this catches it.

**Boundary convention** — an event exactly one window old has fallen out
(`test_event_exactly_one_window_old_has_fallen_out`). Half-open versus closed is the
classic parity day-eater: it produces small, plausible, intermittent differences
rather than an obvious break.

**Bounded memory** — the ring never exceeds capacity, and saturation keeps the
decline counter consistent (two separate tests, because a naive eviction can leave
the counter describing events no longer in the buffer).

**Streaming equals batch** — feeding events one at a time must equal scoring them as
a block (`test_streaming_matches_batch`, and again cross-engine).

**Determinism** — same input, same output, run to run.

**Explainability** — `test_flag_contributions_sum_to_the_score`: the six
contributions must sum to the score, so an explanation can never assert a cause the
arithmetic does not support.

**And the negative invariant**: `test_detector_cannot_see_labels` — the detector's
input type has no label field, so ground truth cannot leak into a score.
---

# 4. THIS PROJECT'S ARCHITECTURE

**Q49. Walk the five-stage pipeline.**

Five stages, one per Rust module, mirrored by the Python reference
(`lib.rs` documents the table; `docs/ARCHITECTURE.md` draws it):

1. **`ingest`** — untyped input becomes a typed `Event`. The boundary where the
   rest of the core gets to rely on its inputs.
2. **`state`** — per-entity state keyed by (axis, identifier), across four axes:
   BIN, IP, device, merchant.
3. **`velocity`** — fixed-capacity ring buffers, sliding-window counts, decline
   counts, amount sums, distinct-card and distinct-merchant maps, maintained
   incrementally on push and eviction rather than recomputed.
4. **`baseline`** — Welford and EWMA per entity, fed on a 60-second sample gate.
5. **`score`** — four evidence terms minus two damping terms, in fixed summation
   order.

Per event the order is: advance **all four** axes (push the event, evict what has
aged out), then score, then fold baselines. Scoring before folding is deliberate —
an event must not be measured against a baseline that already contains it.

`lib.rs` says "there is no sixth module, by decision rather than by omission." That
line exists because the temptation at every stage was to add one.

**Q50. What does `ingest.rs` do and what would break without it?**

It defines the typed `Event` and the `Status` enum (`Approved`/`Declined`), parsing
status from text and rejecting anything else with a typed error.

What it buys is a **narrow, checked boundary**: after ingest, no downstream module
does string comparison on status or wonders whether a field is present. Without it,
status handling would be scattered as string matching across the scoring code, and
a typo would be a silent misclassification rather than a parse error.

The load-bearing detail is what the struct **does not** contain: there is no `label`
and no `scenario_id` field. Ground truth is not merely unused downstream — it is
unrepresentable in the detector's input type. `test_detector_cannot_see_labels`
asserts it on the Python side, and `FEATURE_COLUMNS` in `gen/schema.py` excludes
both. Label leakage is the most common way a fraud project's numbers become
fiction, and the defence here is a type rather than a discipline.

**Q51. What does `state.rs` do and how is it bounded?**

It holds one `EntityState` per (axis, identifier) in a `HashMap`, and it is where
the memory story lives.

`Axis` has four variants — `Bin`, `Ip`, `Device`, `Merchant` — and **no `Card`
variant**, which is the compile-level enforcement of the novelty ban. Only the BIN
axis tracks distinct-entity counts (`tracks_distinct`), because only the BIN score
terms need them; the other three carry counts and sums only, which is a deliberate
memory saving.

The bound is precise and I state it precisely: **retained events are bounded per
entity** (the 512-slot ring), but **entities are never freed**, so total memory is
**linear in distinct entity count**. The test was originally named
`memory_bounded_under_entity_churn`, which overclaimed; it was renamed
`window_memory_bounded_per_entity` at the Phase 3 gate to say what it actually
checks. Measured: 3,874 bytes per entity in Rust, 1,975 in Python, projecting 31 GB
and 16 GB per month at an assumed 8M distinct entities (`BENCH.md` §4). Unbuilt
fixes — LRU eviction or a count-min sketch, each with its accuracy trade — are in
`FAILURE_MODES.md` §7.

**Q52. What does `velocity.rs` do?**

It owns the sliding window: a fixed-capacity ring buffer per entity plus the
aggregates the score needs, maintained incrementally.

Three things make it worth its own module. **The window convention** — `(t−W, t]`,
half-open — is written down here and in `interface.py` because a half-open/closed
disagreement between two implementations produces small, plausible, intermittent
parity differences rather than an obvious break. **Forget-on-push**: aggregates are
decremented on eviction and incremented on push, so the window's count, decline
count, amount sum and distinct maps are always correct without walking the buffer —
that is what makes per-event cost O(1) rather than O(window). And **saturation
handling**: at capacity the oldest retained event is dropped and the entity is
marked saturated, surfaced as `Flag.window_saturated` rather than silently losing
data.

The incremental aggregates are the part most likely to be subtly wrong, which is
why they are property-tested against a brute-force recount rather than unit-tested.

**Q53. What does `baseline.rs` do, and why must it match the Python operation for
operation?**

It holds the two estimators — `Welford` and `Ewma` — and nothing else.

The parity requirement is stricter than "compute the same thing." The comment on
`Welford::update` says it: the update order — increment count, compute delta, update
mean, then accumulate `m2` against the **new** mean — is the same as the reference,
and reordering gives an arithmetically equivalent result that **differs in the last
bits**.

Why that matters: floating-point addition is not associative, so `(a+b)+c` and
`a+(b+c)` can differ in the final ulp. Those baselines feed z-scores, which feed
scores, which get compared to a threshold. A last-bit difference is usually
invisible — until a score lands within an ulp of the threshold, and then one engine
flags an event the other does not, on one event in millions, non-reproducibly. That
class of bug is exactly what the parity fixture is designed to make impossible, and
the reason parity was specified as **bit-exact** rather than "within tolerance."

**Q54. What does `score.rs` do?**

It holds `DetectorConfig`, the `Contributions` struct, and `Detector::update` —
which advances all four axes, scores, then folds baselines, in that order.

Two design points. **The summation order is fixed** and the contributions are
summed in that fixed order, because the order changes the last bits and the Python
reference does it in exactly this order. **The contributions are returned, not just
the total**, which is what makes `Flag.contributions` possible and hence what makes
`test_flag_contributions_sum_to_the_score` meaningful: an explanation can never
assert a cause the arithmetic does not support.

`score_batch` exists alongside `update` so a whole stream can be scored in one FFI
call, and `test_streaming_update_matches_score_batch` asserts they agree — the batch
path is an optimisation, never a different detector.

**Q55. Walk the Python packages.**

Four, each with one job (`docs/ARCHITECTURE.md`):

**`gen/`** — the synthetic stream. `schema.py` (the frozen `Event`, `FEATURE_COLUMNS`
excluding ground truth, the six scenarios), `entities.py` (reserved-range
identifiers), `baseline.py` (Poisson arrivals, diurnal shape, Zipf popularity),
`scenarios.py`, `schedule.py`, `config.py`, `build.py` (byte-identical gzip output,
manifest), `summary.py`, and `ASSUMPTIONS.md` — which is the most important file in
the package, because every metric is downstream of it.

**`detect/`** — `interface.py` (the seam: `DetectorConfig`, frozen `Flag`, the
window convention, "WHAT A FLAG DOES"), `reference.py` (the specification),
`rust_engine.py` (the adapter and the single `make_detector(engine, config)`
resolution point), `parity.py` (the fixture generator and checker).

**`eval/`** — `loader.py` (temporal split, refuses anything else), `metrics.py`
(alerts, time-to-detection, average precision, the vectorised sweep precompute),
`costs.py` + `costs.toml` (the rupee model), `harness.py` (the whole evaluation and
its rendering), `bench.py`.

**`llm/`** — the explanation layer, deliberately unreachable from the other three.

**Q56. Why is the `Detector` seam where it is, and what does that buy?**

The seam is a `Protocol` in `detect/interface.py` with three methods: `update`,
`score_batch`, `reset`. Everything downstream talks to that, never to a concrete
implementation. `make_detector(engine, config)` in `rust_engine.py` is the single
place an engine name resolves to an object.

Three things depend on it. **The engine swap**: the Rust core drops in behind the
same interface, so `make eval ENGINE=rust` is a benchmark rather than a rewrite —
and the fact that both engines produce byte-identical metrics JSONs is a real parity
test over 1.6M events, not a formality. **The parity spec**: `reference.py` is the
written specification the Rust core is tested against, which only works if the
interface is a document rather than an accident. **The LLM boundary**: the seam
emits frozen `Flag` objects, so the explanation layer consumes a value type and has
no route back into the detector.

The seam is deliberately narrow — three methods, no configuration hooks, no
callbacks. A wider interface would have made the Rust adapter harder and the parity
claim weaker.

**Q57. Why is `Flag` frozen?**

Because it is evidence, and evidence that can be edited after the fact is not
evidence.

`Flag` is a `@dataclass(frozen=True, slots=True)` carrying the score, the threshold,
the window aggregates, the baselines they were compared against, the saturation
bit, and the six signed contributions. Attempting to assign to any field raises
`FrozenInstanceError` — asserted by `test_flag_dataclass_is_frozen`, which
specifically checks `score` and `threshold`.

It matters most at the LLM boundary. The explanation layer receives a `Flag` and
returns a `str`. Because the dataclass is frozen, **even a hostile explanation
cannot write back into the evidence** — there is no code path from the explanation
to a score, a threshold, or a label, and that is a property of the type rather than
of my carefulness. `test_explain_does_not_mutate_flag` checks the whole dict before
and after rendering both the prompt and the template.

The `contributions` tuple is the other half of the same idea: the six terms sum to
the score, so an explanation is constrained by arithmetic that travels with the
evidence.

**Q58. Where does state live, and what happens on restart?**

Entirely in memory, in the `StateStore` hash map, one `EntityState` per (axis,
identifier). Nothing is persisted.

On restart, **everything is lost**, and that is a measured failure rather than a
theoretical one. With empty baselines a BIN's baseline window count sits near 1.0
with almost no variance, so ordinary traffic scores 17–21 standard deviations above
it. Measured in `FAILURE_MODES.md` §2.3: cold-started, 29 flagged, 24 attack, **5
false positives**, all `issuer_outage`; warmed, 230 flagged, 230 real, zero false
positives.

The guard that limits the damage is `baseline_min_samples = 20` — entities score 0
until they have 20 baseline samples. The mitigation that would remove the failure —
persisting baselines across restarts — is recommendation 6 in §7 and is **not
built**.

For a production deployment this is a first-class problem, not a footnote: combined
with linear memory growth, the operational statement today is "restart or shard
before the entity table exceeds memory," and every restart re-opens the cold-start
window.

**Q59. Why one score and one threshold rather than per-scenario models?**

Because the operating point has to be a single decision, and because per-scenario
models would need per-scenario labels in production, which do not exist.

At authorization time the system answers one question — decline or not — so
somewhere every signal collapses to one comparison. Doing that collapse explicitly,
with six named terms and visible weights, is more inspectable than three models
whose disagreements would need arbitration logic that is itself untested.

The cost is real and I would concede it: `slow_low` is a genuinely different shape
from `burst` — 48–66 events over 5.5–7 hours versus 190–300 in 9–15 minutes — and
one 5-minute window with one threshold serves them unequally. Event-level recall by
scenario shows it: `slow_low` sits at **0.445**, the lowest of the three, even
though all 20 of its episodes are caught.

The alternative I would actually build first is not per-scenario models but the
**multi-horizon window** (§7 recommendation 1) — the same single score, computed over
two time scales, which addresses both `slow_low` and the outage failure with one
change.

**Q60. What is `parity.py` and why was the fixture committed?**

It generates and checks the parity fixture: a committed stream of events with the
reference detector's scores, which the Rust core must reproduce. `PARITY_CONFIG`
defines the stream, `TOLERANCE` is 1e-9, and `--check` re-verifies the committed
file against a fresh run.

It was committed — and specifically **not gitignored** — because a fixture generated
at test time proves nothing about drift. If both sides regenerate it, they agree
with each other's current behaviour by construction. A committed fixture is a
*record of what the reference produced at a fixed point*, so a change to either
implementation shows up as a diff.

It exists in two formats: `parity.json` (canonical) and `parity.tsv` (a std-readable
twin), so the Rust test can read it with the standard library alone rather than
adding a JSON dependency to the crate. `test_parity_json_and_tsv_agree` keeps them
consistent.

The fixture is 3,866 events and includes a deliberately saturating mega-burst — 900
events in 4 minutes — because the first version never filled a ring buffer at all.
That story is Q133.

**Q61. What does `rust_engine.py` do?**

It is the adapter that makes the Rust core satisfy the same `Detector` protocol as
the reference, plus `columnarise()`, which turns a list of `Event`s into the NumPy
arrays and Python lists the Rust `score_batch` expects, plus `ENGINES` and
`make_detector(engine, config)`.

Two design points worth stating. It is the **single resolution point** — one place
where the string "rust" or "python" becomes an object, so there is no engine
branching scattered through the harness, and `test_make_detector_rejects_unknown_engines`
asserts an unknown name fails loudly rather than silently defaulting.

And the adapter is where the **surface asymmetry** is handled honestly: the Python
reference returns a rich `Flag` with all the evidence on every update, while the
Rust bridge returns a bare `f64` score. The harness only needs scores, so the fast
path exposes only scores; the `Flag` path stays in Python. That is a deliberate
scope boundary — porting the evidence struct across FFI would have doubled the
binding surface for no evaluation benefit — and it is why `BENCH.md` §5 lists
inspectability as the Python engine's advantage rather than pretending the two are
equivalent.

**Q62. What does `cli.py` expose?**

Two subcommands. `spandan replay` walks a stream and prints flags with a running
rupee exposure counter — warmed on the training window by default, with
`--cold-start` to demonstrate the cold-start failure deliberately, and `--engine` to
pick a core.

`spandan explain --flag-id <id>` renders the analyst-facing explanation for one
flag, with `--template` to force the deterministic version and a `CassetteMiss`
fallback that degrades to the template with exit code 3 rather than erroring out.

The `replay` warming behaviour is itself a bug fix with a story (Q131): the demo
originally started cold while `make eval` warmed on train, so the demo flagged the
issuer outage and the evaluation did not. The demo was misleading in the worst
possible direction — it is what a reviewer watches.

**Q63. If you had to delete one module, which and what breaks?**

`llm/` — and the answer to "what breaks" is **nothing**, which is the point.

That is provable rather than asserted. `test_eval_runs_with_llm_import_poisoned`
replaces `sys.modules['spandan.llm']` with an object that raises on any attribute
access, then runs the full evaluation — scoring, the 600-point sweep, constrained
selection — twice, poisoned and clean, and asserts identical score arrays and an
identical selected threshold. The import-graph test asserts `spandan.detect` and
`spandan.eval` cannot even import the package.

So deleting the LLM layer costs the `explain` subcommand and nothing else. No number
in the evaluation would change by a single bit.

Any of the other modules, by contrast, is load-bearing: delete `gen/` and there is no
data, delete `baseline` and the detector has nothing to deviate from, delete `eval/`
and there are no numbers at all. The asymmetry is deliberate — the LLM was scoped
from the start as something that must be removable without consequence.
---

# 5. THE DATA

**Q64. Why synthetic data? Lead with the real reason.**

Because no public dataset carries the fields this detector needs. A card-testing
velocity detector keys on **BIN, IP, and device**, and those three together do not
exist in any public fraud dataset — they are exactly the fields that get stripped
for privacy before release.

That is the disqualifying constraint, and it is stated at the top of
`ASSUMPTIONS.md`. Everything else — determinism, controllable negative controls,
known episode boundaries for time-to-detection — is a benefit that follows, not the
reason.

The cost is severe and I state it before being asked: **all my numbers are
synthetic-stream numbers, and a synthetic stream is a hypothesis about traffic, not
traffic.** `ASSUMPTIONS.md` §2 lists ten specific ways this stream is unlike real
traffic, and the section is explicitly titled "read this before believing any number
from `make eval`." The most damaging are §2.1 (one diurnal shape for every merchant,
so baselines are more predictable than reality — flatters the detector), §2.7 (the
positive rate is roughly ten times realistic — flatters precision), and §2.9
(perfect labels — flatters recall).

**Q65. Why not IEEE-CIS?**

Two disqualifying properties. It is **not card-testing-specific** — it is a general
CNP fraud dataset with a `card1` entity identifier, so the velocity-of-probing
structure this detector exists to find is not labelled in it and may barely be
present. And it is **Kaggle-competition-gated**, which makes a clean held-out story
awkward.

More fundamentally it lacks the axes: no BIN, no IP, no device in the form this
design keys on. I would be selecting a proxy entity and calling it a BIN, which is
the kind of quiet substitution that makes an evaluation unfalsifiable.

`RESEARCH.md` records the assessment, and also records the one use I considered and
did not take: using its benign distribution as a sanity anchor for realism, without
adopting its labels. That remains a reasonable thing to do and I did not do it —
**UNANSWERED: no external anchor was ever run against this generator's benign
distribution.** It is the cheapest available answer to "your data is made up," and
its absence is a genuine hole.

**Q66. Why not PaySim, Sparkov, or the Kaggle `creditcardfraud` set?**

Each fails on a specific property, not on general unsuitability.

**PaySim** is mobile-money — transfers and cash-outs, about 6.3M rows at 0.13%
fraud. The fraud shape is account-draining, not authorization probing. There is no
authorization/decline concept at all, which is the primary signal here.

**Sparkov** is generic synthetic CNP transactions — the fraud is not card testing,
and being synthetic itself it would give me someone else's assumptions with none of
the documentation I can offer for mine.

**Kaggle `creditcardfraud`** — 284,807 rows, 0.17% fraud — is **anonymised PCA
components**. There is no BIN, no IP, no device, no merchant, and no interpretable
feature of any kind: V1 through V28 plus Time and Amount. A velocity detector cannot
be built on it, and nothing about it can be explained to an analyst.

`RESEARCH.md` also cites work indicating that off-the-shelf synthetic tabular
generators destroy exactly the velocity and multi-account signals card-testing
detection depends on — which is the argument for a purpose-built generator rather
than a general one.

**Q67. Walk each scenario and its statistical signature.**

Six, three attacks and three controls (`ASSUMPTIONS.md` §1.7), described as
signatures only — rate, concentration, amount band, decline ratio:

**`burst`** (label 1): one BIN, one IP, one device, 190–300 distinct cards, ₹1–₹60,
decline ratio 0.83–0.89, over 9–15 minutes. The loud case.

**`rotating`** (label 1): same BIN and card concentration, but IP and device spread
across 55–74 values, over 22–31 minutes. No single address carries unusual volume —
built specifically so the per-IP axis misses it, which is what makes the
drop-per-IP ablation informative.

**`slow_low`** (label 1): same concentration, 48–66 events over 5.5–7 hours, decline
ratio 0.69–0.75. Deliberately sits underneath a fixed per-window count — the case a
naive rule cannot catch.

**`flash_sale`** (label 0): 760–1,020 distinct cards over 45–60 minutes, many
issuers, ordinary amounts, decline ratio 0.14–0.17. Negative control on the
**volume** axis.

**`issuer_outage`** (label 0): one BIN, 55–78 minutes, 760–1,050 events across only
~30% as many cards, ordinary amounts, decline ratio 0.79–0.86, spanning 4–5
merchants. Negative control on the **decline-ratio** axis.

**`outage_single_merchant`** (label 0): the same outage at **one** merchant with
probe-band amounts. The hardest control by a wide margin.

**Q68. Why does an attack episode borrow a BIN that also carries benign traffic?**

Because inventing a fresh BIN for each attack would hand the detector a free
separator, and because it is what actually happens: an issuer whose cards are being
tested still has ordinary customers transacting.

Strictly harder, too. A borrowed BIN arrives with a **legitimate baseline** — an
established EWMA centre and Welford spread built from real traffic — so the attack
has to deviate from a real distribution rather than appear from nowhere against an
empty one. Against an empty baseline the cold-start effect alone would flag it, and
I would be measuring that rather than the detector.

`test_attack_bins_also_appear_in_benign_traffic` asserts it, so the property cannot
silently regress.

This is one of the places where the generator is deliberately unkind to the
detector, and I would offer it early to a skeptic: the three easy shortcuts — fresh
BINs, zero benign decline rate, and card novelty — are all closed off, the first two
by construction and the third by a binding ban.

**Q69. What does the flash-sale control test, and what did it get wrong first?**

It tests the **volume** axis: can the detector tell a legitimate surge from an
attack? 760–1,020 distinct cards in 45–60 minutes, many issuers, ordinary amounts,
low decline ratio. Result at the constrained operating point: **20 flagged events of
17,787**, a rate of 0.11%. The volume axis is handled.

What it got wrong first is the best bug in the project. Benign traffic draws cards
with **Zipf(1.35)** popularity weights, so most of the 9,000-card pool never
transacts. The flash sale drew **uniformly** over the same pool — so roughly a third
of its "known customers" were identifiers appearing nowhere else in the stream, and
the control was partly separable by novelty alone.

Caught by `test_flash_sale_is_a_mixture_of_known_and_new_customers`, which asserted a
property of the output — known-customer share between 55% and 95% — and failed at
**66%**.

The fix made the mixture explicit: 75% drawn with the *same* popularity weights as
benign traffic, 25% genuinely new. The **realised** share on the shipped dataset is
**43.7%** known against 56.3% first-time, measured on every build and recorded in
`manifest.json` — and the
generator was deliberately **not** re-tuned to hit 75%, because adjusting the pool
size to make a parameter come out right is fitting the data to a number I invented.

**Q70. What would the Zipf bug have done to your metrics if it had survived?**

It would have inflated precision, silently, with no failing test anywhere — and I
would have reported the inflated number in good faith.

The mechanism: `flash_sale` is the negative control on volume. If a third of its
customers are cards that appear nowhere else in the stream, then "share of
never-before-seen cards" separates sales from attacks partly for free. Any detector
with novelty sensitivity — even implicit — scores well on the control for a reason
that has nothing to do with detecting card testing.

The damage is not bounded to that one scenario, because precision is computed
against **all** clean events. A negative control that is easier than it should be
makes every precision figure downstream optimistic, and the headline of this project
is a precision number.

The worst property is that it would never have failed anything. It would have
surfaced in Phase 2 as **suspiciously good precision** — which is the hardest kind of
error to notice, because it looks like success. That is the same shape as the
single-seed precision of 1.00 (Q128) and it is why this is instance zero of the
plausible-number pattern.

**Q71. Why is the ban on card-novelty features binding rather than advisory?**

Because the flash-sale control only **partially** controls for novelty, and I can
quantify by how much. Cards never seen elsewhere in the stream: attack scenarios
**100%**, `flash_sale` **~42%**, outages **~10%** (`ASSUMPTIONS.md` §1.7a).

So a novelty feature would separate attacks from controls partly for free, and the
reported precision would measure an artifact of my generator rather than a property
of the detector. The constraint is written as: no feature may be derived from card
novelty, first-seen-ness, or distinct-card counts used as a novelty proxy — and if
one is ever added, **the flash sale ceases to be a valid negative control and the
negative case must be rebuilt before any metric is reported.**

Enforced at three strengths, which is the part worth saying out loud: a documented
promise in ASSUMPTIONS; a test that scans the detector packages
(`test_no_card_novelty_feature_exists_anywhere`) plus one that checks no
card-novelty state is retained; and the Rust `Axis` enum having no `Card` variant,
which makes it a **compile error**. Three enforcement strengths, and the type
system's is the strongest — a documented promise is what everyone offers.

**Q72. What does `ASSUMPTIONS.md` §2 disclose?**

Ten specific ways this stream is unlike real traffic, under a heading that says to
read it before believing any number. The ones that change how the results should be
read:

**§2.1** One diurnal shape for every merchant — baselines are more predictable than
reality, which likely makes the detector look **better**. **§2.2** No trend,
seasonality or holidays — drift is untested. **§2.3** Stationary benign decline
rates apart from the modelled outages; the milder real phenomenon (gateway blips
lifting declines a few points for minutes) is unmodelled. **§2.4** Poisson arrivals,
so benign traffic is less bursty than reality and the flash sale is the only benign
surge tested. **§2.5** Cards, IPs and devices drawn independently — no shared-IP
structure, so the per-IP feature is never tested against a legitimate reason for
concentration (corporate NAT, CGNAT, households). **§2.6** No geography, MCC, 3DS,
issuer identity or decline reason codes. **§2.7** Positive rate ~1.33%, far above
real merchant rates — **any precision figure here is an upper bound**. **§2.8**
Attacks are single-merchant while outages span merchants, so merchant span is a
stronger separator here than it would be against a real multi-merchant campaign.
**§2.9** Perfect labels. **§2.10** Constant decline ratio within an episode.

§2.8 is the one I would volunteer, because it means one of my damping terms is
flattered by construction.

**Q73. How do you defend this data to a skeptic who says "you graded your own
homework"?**

I do not fully defend it — I bound it. Four moves, in order.

**One: the constraint was real.** No public dataset has BIN, IP and device together;
that is why the stream is synthetic, and I can name the specific disqualifying
property of each candidate (Q65, Q66).

**Two: the generator is deliberately unkind.** Benign decline rates are 4.5–11.5%,
not zero, so decline ratio is not a free separator. Attack BINs carry benign traffic,
so attacks must deviate from a real baseline. Card novelty is banned outright and
enforced by the type system. Each of those closes a shortcut that would have
flattered the results.

**Three: the negative controls attack my own primary signal**, and one of them wins.
`outage_single_merchant` gets **50.5%** of its events flagged with headroom −267%.
If I were grading my own homework I would not have built the control that fails, and
I would certainly not have made it the headline of the failure-modes document.

**Four: the assumptions are enumerated and directional.** §2 states which way each
distortion pushes, and three of them say the results are flattered.

What I cannot claim is external validity. The correct closing sentence is that these
numbers are a **lower bound on the engineering and an unvalidated estimate of the
performance**, and the first thing I would do with real data is re-run the whole
evaluation rather than port the numbers.

**Q74. How is the stream made deterministic, and why does that matter?**

One `SeedSequence` spawns independent generators for pools, merchants, benign
traffic, and **one per episode**. Independent per episode is the important part:
retuning one scenario does not shift the draws of every other, so a before/after
comparison actually means something. Without that, every edit silently rewrites the
whole dataset.

Output is gzipped JSONL written with **`mtime=0`**, because gzip otherwise stamps
the current time into the header and two identical runs would produce different
bytes. `test_seed_reproducible_byte_identical` asserts same seed, same bytes.

The start timestamp is a fixed constant, never "now" — a dataset that changes when
you regenerate it next month is not a held-out test set.

Why it matters beyond tidiness: the entire parity claim rests on it. Bit-exact
agreement between two engines is only checkable if the input is bit-identical, and
the committed parity fixture is only meaningful if the reference can be re-run to
produce the same thing.

**Q75. Why 100 days rather than 14, and how do you know that was not cheating?**

Because two episodes per scenario in the test window is an anecdote, not a
measurement. At 14 days, per-scenario recall swung **0.16–0.54** across three seeds —
every per-scenario number rested on a sample of two.

The fix had to raise statistical power **without making the task easier or harder**.
So the episode rate per day was held constant — about 0.4 per scenario per day — and
the stream was made longer: 100 days, 240 episodes, 20 per scenario per split.

That distinction is the whole answer to "was that cheating." Packing more episodes
into the original 14 days would have been a difficulty change wearing a statistics
costume: more attack traffic per day means more attack traffic folded into the
per-BIN baselines those attacks are measured against, which changes the problem.
`test_more_episodes_came_from_a_longer_stream_not_a_denser_one` asserts the density
is unchanged, so it cannot silently drift later.

The result: recall's cross-seed spread fell from 0.386 to **0.092**. Precision's did
not, because precision is dominated by false positives on one control whose volume
varies with the stream (§2.1) — which is a real finding rather than a residual
noise problem.

**Q76. How do you know no attack episode straddles the train/test boundary?**

`test_no_episode_straddles_the_split_boundary` asserts it directly, and the split is
described as a wall in `ASSUMPTIONS.md` §1.1: train is days 0–50, test days 50–100,
strictly later, no event crossing.

It matters because a straddling episode is a subtle form of leakage: the detector
would enter the test window with an entity already mid-attack and its baselines
already shaped by the attack's own traffic. Time-to-detection for that episode
becomes meaningless — you cannot measure how fast something was caught if it began
before the clock started — and the episode-level recall figure gets a free pass.

The related guard is on the split itself: `loader.py` raises `NonTemporalSplitError`
rather than proceeding if the split is not strictly temporal. The failure mode I am
protecting against is not that I would deliberately shuffle — it is that a later
refactor might load the data differently and nobody would notice a random split had
crept in.

**Q77. What is in the manifest and why?**

A record of what each build actually produced: the SHA-256 of the full config,
per-scenario event and episode counts, and the `negative_case` block carrying the
**realised** flash-sale known-customer share (43.7% known, 56.3% first-time).

The point of recording realised rather than configured values is the Zipf bug in
miniature: the configured fresh-draw share was 25% and the realised first-time share
is 56.3%,
and if the manifest recorded the parameter I would be reporting a number that is not
what the data contains. `test_scenario_positive_counts_match_manifest` checks the
manifest against the built stream, so the two cannot drift.

The config hash means any figure in any document can be traced to the exact settings
that produced it — which matters in a project where the stream is regenerated
several times across phases and each regeneration invalidates the previous numbers.

**Q78. Your generator writes both the attacks and the "normal" traffic. Isn't the
detector just finding what you planted?**

Partly yes, and the correct response is to say where that is true and where the
design fights it.

Where it is true: the attack signatures are mine, so the detector is being tested
against my hypothesis of what card testing looks like. If real card testing has a
different shape, none of this transfers. I cannot rule that out — `ASSUMPTIONS.md`
§3 says explicitly that a published distribution of real episode sizes and durations
would change §1.7 and none was available.

Where the design fights it: **the negative controls were written to attack my own
detector's primary signal, and one of them succeeds.** `issuer_outage` produces
82.5% declines on one BIN from entirely legitimate traffic — feature for feature the
card-testing signature with no attacker. `outage_single_merchant` then strips the two
separators the detector was actually using (merchant span and amount) after Phase 2
measured that those, not retry structure, were doing the work. Result: **50.5%
flagged, headroom −267%.**

If I were only finding what I planted, that control would pass. It fails, it is the
headline of the failure-modes document, and the mechanism behind it (Q46) is worked
out to the level of "1.13 in-window attempts per card versus 1.22" — which is a
finding about my detector, not about my generator.
---

# 6. THE EVALUATION

**Q79. What is a temporal split and why is it non-negotiable here?**

Train on the past, test on the future, with a hard boundary. Here: train days 0–50,
test days 50–100, no event crossing, asserted by
`test_no_episode_straddles_the_split_boundary` and enforced by `loader.py`, which
raises `NonTemporalSplitError` rather than proceeding if the split is not strictly
temporal.

Non-negotiable because a **streaming detector carries state across events**. A random
split does not merely leak labels — it destroys the thing being measured. The
baselines that make `velocity_bin` meaningful are built from prior events on the same
entity, so a shuffled split gives the detector entity history from *after* the event
it is scoring. Every z-score would be computed against a future it will not have in
production.

`RESEARCH.md` names the random-split-plus-SMOTE-plus-XGBoost pattern as the standard
student submission a fintech risk panel dismisses on sight, and the temporal split is
the first of the "do the opposite" list.

**Q80. What would a random split have shown?**

**UNANSWERED — I never ran one, and no number in this repo answers it.** The loader
refuses to build a non-temporal split at all, so producing the comparison would mean
deliberately bypassing the guard.

What I can say is the direction and the mechanism, not the magnitude. It would have
been better, possibly dramatically, for three compounding reasons: baselines
contaminated with future events on the same entity; episodes split across train and
test so the detector sees part of the same attack it is being tested on; and
per-entity structure — a BIN's characteristic decline rate — shared across the
boundary.

If a panel presses, the honest framing is: "I built the guard instead of the
comparison. Running it once as a documented demonstration of the gap would have been
a cheap and persuasive experiment, and I did not do it." That is a fair criticism —
it is exactly the kind of measurement this project claims to value, and its absence
is noted in §13.

**Q81. What is the validation window and how is it constructed?**

The **last 25% of the training period**, by time — a suffix, never a sample
(`loader.py`, `validation_fraction=0.25`). The earlier part of train becomes
`train_warmup`, which the detector runs over to build baselines before scoring
begins.

It is a suffix rather than a random sample for the same reason the main split is
temporal: sampling validation from anywhere inside train would give threshold
selection the benefit of future events on the same entities.

Everything tunable is chosen here and nowhere else: the threshold, and in principle
the weights and window size. **The test window is read once, after the operating
point is fixed.** That is the discipline the whole evaluation rests on, and the
budget constraint that gates the selection was registered in `costs.toml` before the
test window was read at all — commit `e5b48f8`, 2026-08-24.

The warm-up detail matters operationally too: `score_split_once` warms on train
before scoring test, which is why the CLI demo was changed to warm as well (Q131) —
a cold demo and a warm evaluation were two different detectors.

**Q82. Why do you report three precisions?**

Because they answer three different questions and quoting only the first would be
the flattering choice.

**Event-level, 0.4462**: of the transactions flagged, what share were card testing.
This is the detector's raw discrimination on this stream.

**Alert-level, 0.433**: of the 487 alerts a human actually opens, how many are real —
211 of them. This is what the analyst experiences, and it is almost never reported
in student projects or, frankly, in many papers. It differs from event-level because
alerts are deduplicated per (merchant, BIN) with a 15-minute cooldown, so the two
populations are not the same population.

**At 0.15% prevalence, 0.0824**: what either would be at a realistic merchant base
rate — roughly eleven false alarms per true catch. **This is the headline**, because
the generator's own positive rate is ~1.33%, about ten times realistic, and reporting
0.4462 as the result would flatter the detector by an order of magnitude.

The reweighting is `costs.py::reweight_to_prevalence`, and
`test_reweighting_leaves_recall_unchanged` asserts recall is invariant under it —
which is the property that proves the transformation is legitimate.

**Q83. Walk every parameter of the rupee cost model and its source.**

Five groups, all in `eval/costs.toml`, each carrying its own `basis` field — and
three of the five say ASSUMPTION rather than citation, in the file itself.

**`auth_fee`**: ₹1.50 per blocked attempt. **ASSUMPTION.** Razorpay's public pricing
is charged on successful payments, so a per-attempt authorization cost is contract-
and scheme-dependent rather than published. Set deliberately low so the headline
saving does not lean on it.

**`chargeback`**: ₹500 fee, loss fraction 1.0 of the amount, **0.8 rate on approved
fraud**. The fee basis cites Razorpay's published handling range of ₹200–750 for
standard categories; ₹500 sits mid-range. The 0.8 rate is explicitly **not citable**
— it assumes a card confirmed live is subsequently used and disputed.

**`blocked_good`**: contribution margin **0.25**, and `only_charge_if_would_have_been_approved
= true`. The margin is an ASSUMPTION at a mid-range Indian e-commerce figure —
counting the full ticket would overstate false-positive cost several-fold. The
approval flag matters enormously for the outage controls, where ~82% of traffic
declines regardless; ignoring it would inflate that scenario's FP cost roughly 5×.

**`review`**: ₹40 per alert. **ASSUMPTION, uncitable** — roughly five minutes of
analyst time. Used *only* for the illustrative headline line.

**`prevalence`**: 0.15% target rate. **ASSUMPTION, not a citation** — no public
per-merchant card-testing prevalence figure was available at build time.

The honest summary: the net position is dominated by the chargeback term, which is
the product of **two uncitable assumptions**, and halving the assumed rate roughly
halves the headline saving (`FAILURE_MODES.md` §6).

**Q84. What is the break-even inversion and why did you do it?**

Instead of asserting what an alert review costs and reporting a net figure that
depends on it, the harness **inverts the relationship** and reports the review cost
at which the detector stops paying for itself: `gross / alerts`, in
`costs.py::break_even_review_paise`.

The number is **₹613 per alert** at the constrained operating point (487 alerts, 9.7
per day). The claim becomes "the detector pays for itself as long as reviewing an
alert costs under ₹613" — which a merchant can evaluate against their own staffing
costs without accepting any assumption of mine.

I did it because the review cost was the weakest input in the model — ₹40 an alert
is roughly five minutes of analyst time and is uncitable — and a headline that rests
on an uncitable input is a headline a panel can dismiss in one question. Inverting it
turns the weakest assumption into an **output**, which is strictly more defensible.

The caveat I attach every time: break-even counts only what the review queue costs.
It prices none of the 11,216 legitimate transactions declined — whose cost the model
puts at ₹8,595 and whose cost in customer trust it does not model at all.

**Q85. What is the budget frontier, and why is it a sensitivity analysis rather than
a menu?**

It is the table `make eval` prints showing what the operating point would be at each
alert budget — 2, 5, 8, 10, 12, 15, 20, 30, 50 — with threshold, alerts/day,
events/alert, flag rate, all three precisions, recall, episodes, TTD, and both
validation and test net.

It is a sensitivity analysis because **the budget was fixed before test was read**
(commit `e5b48f8`) and the frontier exists so a reader can see how the answer moves
if they disagree with my staffing assumption, not so I can pick a row.

The distinction is not academic, because **budgets 2 and 5 are better on test net
and better on precision** — budget 2 gives precision 0.2034 at the realistic base
rate against my headline's 0.0824. Choosing that row after seeing this table would
be selecting on the test set. The headline stays at 10 because its basis — what one
analyst can plausibly work through — does not depend on the results.

`FAILURE_MODES.md` states the one legitimate escape: re-registering the budget is a
valid decision **provided it is argued from the operational basis and not from the
test column.** I did not re-register it.

**Q86. The frontier's net column is not monotone in the budget. Is that a bug?**

No, and working out why is a good illustration of what the table is measuring.

A larger budget permits every threshold a smaller one permits, so the constrained
maximum **cannot fall** — and on the **validation** window, where selection happens,
it does not: ₹82,806 → ₹100,588 → ₹114,196 → ₹119,267, rising monotonically.

The `test net` column is measured at a threshold chosen on validation, so the two
need not agree. Where test net falls as the budget loosens, that is the
**validation-to-test generalisation gap**, not an arithmetic error.

And it is worth reading as evidence rather than noise: the operating point that
looked best on validation (budget 10, val net ₹119,267) transferred **worst** (test
net ₹279,151, against budget 2's ₹324,539). That is a small, concrete demonstration
that selection pressure costs generalisation — measured on my own selection
procedure, in my own table.

I checked this specifically because a reviewer asked whether it was a bug, and the
resolution is now in the caption where anyone reading the table will hit it.

**Q87. Why 600 threshold points rather than 60?**

Because alerts/day is extremely steep in the threshold, and a coarse grid picks a
lucky point.

The concrete failure: the first frontier used 60 points and produced budgets 10 and
20 with **identical rows** — alerts/day jumped from 6.2 to 27.6 with nothing sampled
between, leaving the most interesting part of the trade-off invisible.

Worse, the coarse grid produced a **better-looking result** that was luck. It
reported the constrained criterion improving precision from 0.400 to 0.487; the
600-point grid finds a higher-validation-net point inside the same budget and gives
0.418 → 0.446. The refined answer is less flattering and is the correct one.

The superseded figures are marked superseded in `FAILURE_MODES.md` §0.1 rather than
quietly replaced — that is a house rule in this project, and it exists because a
reversed conclusion that gets deleted gets re-learned.

The cost of 600 points was addressed separately: the sweep was vectorised
(`SweepPrecompute`, `alert_count_at`, `gross_at`) taking it from ~100 seconds per
call to 0.36s, with `test_vectorised_sweep_matches_loop` asserting exact agreement
with the per-event loops.

**Q88. Why multi-seed, and what did it settle?**

Because nothing in this document should be read from a single stream, and a
single-seed number in a project like this is an anecdote.

Three independently generated 100-day streams. At the constrained operating point:
precision **0.4117 / 0.4462 / 0.5972** (min/median/max), recall **0.8189 / 0.8444 /
0.9106**, PR-AUC 0.6192 / 0.6615 / 0.7928, thresholds 21.99–24.53.

What it settled: **recall is stable, precision is not.** Recall's spread fell from
0.386 to 0.092 when the test window went from 6 attack episodes to 60 — that was the
underpowered-evaluation problem and it is largely fixed. Precision still swings
0.41–0.60, and the reason is diagnosable rather than mysterious: it is dominated by
false positives on one control (`outage_single_merchant`), whose volume varies with
the stream.

What it did not settle: the ablations. §3's null result is a **measurement**
limitation — three seeds could not resolve the EWMA delta against its own variance —
and variance reduction (common random numbers across variants) was never attempted.
That is recommendation 4 in §7.

The origin story is that a single seed once reported **precision 1.00**, which is
instance one of the plausible-number pattern (Q128).

**Q89. Tell me about the ablation retraction.**

Phase 2 claimed that dropping the per-entity EWMA baseline improved net position by
₹17,159, and that EWMA "is not carrying the detection signal." Measured on **one**
stream.

**That claim was retracted.** Across three seeds at the unconstrained operating point
the delta was ₹12,981 median with a range of **[−₹43,496, +₹109,443]** — it changed
sign across streams. And the opposite claim, that EWMA is vindicated, was **refused
on the same evidence**. It was reported as a null result.

The part I am proudest of is the third pass. When the operating point changed to the
constrained one, the measurement was **run again** rather than the old conclusion
carried forward. At the constrained point both ablations beat the full detector on
net rupees consistently, 3 of 3 seeds — drop-EWMA by ₹32,933 median, drop-per-IP by
₹15,560.

And that still does not license "EWMA is useless," for a reason established *before*
the table existed: net rupees varies 1.3× across the entire operating range while
precision at a realistic base rate varies 2.7×. Winning consistently on a metric
demonstrated to be blind to the axis that matters is weak evidence, however
consistent. On precision the ordering reverses — drop-EWMA costs 0.367 against
0.446, ranges barely overlapping.

The full three-pass history is kept verbatim in `FAILURE_MODES.md` §3. A number you
cannot defend is not a result, whichever way it points.

**Q90. Both ablations beat the full detector on net rupees. Why ship the full
detector?**

Because net rupees is the metric I have already demonstrated cannot see the thing
that matters, and I demonstrated that before this table existed rather than after it
was inconvenient.

The evidence: across the whole frontier, net varies **1.3×** (₹254k–₹324k) while
precision at a realistic base rate varies **2.7×** (0.074–0.203). A cost figure that
nearly cannot distinguish the good operating point from the bad one is not a
decision procedure.

On the axis that can see it, the ordering reverses. drop-EWMA costs precision
consistently — 0.367 median against full's 0.446, with ranges [0.37, 0.40] and
[0.41, 0.60] barely overlapping — and costs recall (0.793 vs 0.844). **It buys money
by declining more traffic**, which is exactly the trade the merchant pays for and the
cost model under-prices.

There is also a modelling reason: review cost is **linear** at ₹40 an alert, so 27
alerts/day and 4 alerts/day differ by about ₹900 a day — noise next to the chargeback
term. Linear review cost understates alert fatigue, which is real and unmodelled.

So the recommendation in §3.1 is: argue for the full configuration on precision and
decline rate, and **state plainly that both ablations beat it on net rupees across
every seed.** `drop-per-IP` is the genuinely awkward one — it beats full on all
three — and the honest statement is that the per-IP axis is **unsupported by the
ablation rather than validated by it.** No component is vindicated here.

**Q91. How do you measure time-to-detection and what does it show?**

Events elapsed between an episode's first event and its first flag, plus the rupee
exposure accumulated in that gap, per scenario, at the constrained operating point.

| scenario | caught | median events to first flag | p90 | median exposed |
|---|---|---|---|---|
| `burst` | 20/20 | 0 | 32 | ₹26 |
| `rotating` | 20/20 | 0 | 6 | ₹0 |
| `slow_low` | 20/20 | 1 | 7 | ₹11 |

**And the medians should be read with suspicion, because a median of 0 is exactly
what over-triggering looks like.** Catching every episode on its first event is
trivially achievable by flagging almost everything, and at this operating point the
detector flags 2.52% of all traffic. The alert budget did not prevent it — dedup
collapses 42 flagged events into each alert, so an event-level flood sits inside a
10-alerts/day cap.

**p90 is the number to quote**, because it is not saturated: 32 events for `burst`,
6 for `rotating`, 7 for `slow_low`. On burst episodes of 190–300 events, a p90 of 32
is a genuinely good result.

An earlier addendum reported medians of 2/4/2 from the 60-point grid. Those are
superseded — the finer grid selects a lower threshold inside the same budget and the
medians saturate to 0. The honest reading is that **the median was never the number
to quote here.**

**Q92. Why is `slow_low` your weakest scenario?**

Because it is designed to sit underneath the detector's window. 48–66 events spread
over 5.5–7 hours means any given 5-minute window sees roughly one event — so
`velocity_bin`, the primary velocity term, has almost nothing to work with, and only
the decline-ratio and amount terms carry it.

Event-level recall for `slow_low` is **0.445**, the lowest of the three (burst 0.962,
rotating 0.825), even though
all 20 episodes are caught and the median time to first flag is 1 event.

The gap between those two facts is the interesting part: **episode-level detection
and event-level recall diverge most on the slow scenario.** The detector notices the
episode but flags only a quarter of its events, which for an inline control means
most of the probing gets through even though an alert was raised.

There is a history worth stating: in Phase 2 `slow_low` caught 1 of 2 episodes at
1.7% event recall, and now catches 20/20. **None of that came from a detector
change** — the detector is unchanged. It came from the operating point moving and the
test window growing from 2 episodes to 20. That is a good illustration of how much an
underpowered evaluation can hide.

**Q93. What does "60/60 episodes caught" actually mean, and is it as good as it
sounds?**

It means every one of the 60 attack episodes in the test window produced at least one
flag. It is a real result and it is **not** as good as it sounds on its own.

Two qualifications I would give unprompted. First, at this operating point the
detector flags 2.52% of all traffic and declines 1.41% of legitimate traffic —
catching every episode is much easier when you are flagging a lot. The p90
time-to-detection figures are the ones that survive that objection (Q91). Second,
episode-level detection is the **easiest** of the three recall-ish numbers: event
recall is 0.844 overall and 0.445 for `slow_low`, so "caught" can mean "flagged one
event of sixty."

For an alert-only deployment, episode-level detection is close to the right metric —
one flag raises the alert and a human takes it from there. For the **inline control
this actually is**, event recall matters much more, because every unflagged probe is
one that succeeded.

That is why the README quotes 60/60 next to the decline rate rather than on its own.

**Q94. Someone says your evaluation is over-engineered for a detector this simple.
Answer.**

I would agree with the observation and disagree with the conclusion.

The detector is genuinely simple: six terms, hand-chosen weights, one threshold. The
evaluation is large — temporal splits, three precisions, prevalence reweighting, a
rupee cost model with break-even inversion, a nine-point budget frontier, a
three-seed matrix, ablations with a retraction history, per-scenario
time-to-detection, and three negative controls.

That ratio is the argument, not an accident. **The evaluation is what makes the
detector's numbers believable, and it is also what found everything wrong with the
detector.** Every significant negative finding in this project came out of the
evaluation apparatus: the outage failure came from a control built to attack the
primary signal; the over-triggering came from reading events-per-alert; the
cost-model blindness came from the frontier; the ablation null came from
multi-seed.

A simple detector with a serious evaluation tells you exactly what it can and cannot
do. A sophisticated detector with a weak evaluation tells you nothing you can trust —
and that is the standard student submission `RESEARCH.md` describes: SMOTE, XGBoost,
random split, 99% accuracy, and no idea whether any of it is real.
---

# 7. RUST AND SYSTEMS

**Q95. Why Rust for this, honestly?**

Three reasons, and I would rank the third first because it is the one people do not
expect.

**Type-level guarantees.** The card-novelty ban is the binding design constraint of
this project, and in Rust it is a **compile error**: `Axis` has variants for Bin, Ip,
Device and Merchant, and no `Card`. In Python it is a test; in the docs it is a
promise. Three enforcement strengths, and the type system's is the strongest — a
future contributor cannot add a card-keyed baseline without the compiler stopping
them.

**Latency and its tail.** This is an inline authorization control, so the number that
matters is p99, not throughput. Measured: **p99 24.8µs against Python's 119.3µs**,
with 120,053 events/s streaming against 21,766. A single thread covers roughly 10
billion events/day of headroom. In a control sitting in the auth path, the tail is
the product.

**Predictability.** No GC pauses, no allocation on the steady-state path, contiguous
ring buffers with good cache behaviour.

And the honest counterweight, which `BENCH.md` §5 states in the same sentence as the
win: **it costs twice the memory per entity** — 3,874 bytes against 1,975 — projecting
31 GB versus 16 GB a month at 8M entities. Never the throughput table without the
memory slope beside it.

**Q96. Explain ownership and borrowing as they actually appear in this codebase.**

Ownership: every value has exactly one owner, and when the owner goes out of scope the
value is freed. No GC, no manual free, no double-free.

Where it shows up here concretely: **`Event` owns `String`s rather than borrowing
`&str`**, and `ingest.rs` documents why — the detector **retains events in per-entity
ring buffers for the length of a window**, so borrowing from a caller's buffer would
require those borrows to outlive the call, which the borrow checker will not allow and
which would be genuinely unsafe. The ownership model forced an honest design decision
that a GC language lets you make by accident.

That decision has a measurable cost, which is the second half of the memory story:
each retained ring slot owns two heap `String`s (card, merchant) where Python shares
references to existing string objects. That is one of the two identified causes of the
2× memory gap (`FAILURE_MODES.md` §7), and the fix — interning identifiers to `u64`
handles — is written down and not built.

Borrowing: `&mut self` on `Detector::update` means the compiler guarantees no other
reference to the detector state exists during the update. For a streaming state
machine that is exactly the invariant you want, and it is enforced rather than
documented.

**Q97. Where do lifetimes actually appear here?**

Mostly in the PyO3 bridge, and one place they were removed.

In `pybridge.rs::score_batch` the signature carries `'py` throughout —
`PyReadonlyArray1<'py, i64>`, `&Bound<'py, PyList>`, returning `Bound<'py,
PyArray1<f64>>`. That lifetime ties every borrowed Python object to the duration of
the GIL token `py`. It is what makes zero-copy safe: the Rust slice borrowed from the
NumPy buffer **cannot outlive the Python scope that guarantees the buffer is alive**,
and the compiler enforces it rather than a comment asking me to be careful.

The place they were removed: clippy flagged a needless lifetime on `axis_key` during
Phase 3 and it was deleted. Worth mentioning because it is the honest shape of
lifetimes in application code — they mostly appear at FFI boundaries and are mostly
elided everywhere else.

In the core detector modules there are almost no explicit lifetimes at all, because
the data structures own their contents. That is a design consequence: owned `String`s
in the ring buffer cost memory and buy a codebase with no lifetime plumbing.

**Q98. What does PyO3 do, and what does your binding actually expose?**

PyO3 generates the glue between Rust and CPython: it turns a `#[pyclass]` into a
Python type, `#[pymethods]` into methods, and handles conversion, reference counting
and error mapping across the boundary.

The binding here is deliberately small — `PyDetector` with `new` (keyword arguments
mirroring `DetectorConfig`), `update`, `score_batch`, `reset`, plus three
introspection methods (`entity_count`, `buffered_events`, `saturated_entities`) and
`__repr__`.

Two design decisions worth defending. **`update` returns a bare `f64`, not a `Flag`.**
Porting the whole evidence struct across FFI would have doubled the binding surface,
and the evaluation only needs scores — the rich `Flag` path stays in Python. That is
why `BENCH.md` lists inspectability as the Python engine's advantage rather than
pretending the engines are equivalent.

**The introspection methods exist for the memory benchmark.** `entity_count` is what
lets `BENCH.md` report bytes *per entity* rather than a total RSS figure that means
nothing without a denominator. Exposing them was a measurement requirement, not
API completeness.

**Q99. What is abi3 and what does it buy?**

Normally a CPython extension is compiled against one version's C API and you ship a
separate wheel per Python version. **abi3** is the stable subset of that API: build
once against a minimum version and the same binary works on that version and every
later one.

Here it is `abi3-py310` (`Cargo.toml`), so one wheel covers Python 3.10 and up, which
matches `requires-python = ">=3.10"` in `pyproject.toml`.

What it buys: one artifact instead of four, and no build matrix. What it costs: the
stable API is a subset, so some fast paths are unavailable — irrelevant here, because
the numeric work crosses the boundary through NumPy buffers rather than through
CPython object APIs.

The related packaging detail that cost real time is in `BUILD_LOG.md` entry one: in
maturin's mixed layout the compiled extension must live **inside** a package directory
under `python-source`, so the module is `spandan_core._native` with a Python
`__init__.py` re-exporting its surface. The first diagnosis — that abi3-py310 needed a
3.10 interpreter present — was wrong, and the log records the wrong diagnosis
alongside the right one.

**Q100. Explain the zero-copy claim precisely, including what is not zero-copy.**

`score_batch` takes `ts`, `amount_paise` and `declined` as `PyReadonlyArray1`, which
**borrows the NumPy buffer directly** — `as_slice()` gives a Rust slice pointing at
the same memory Python owns. No copy, no allocation, for the numeric columns.

What is **not** zero-copy, stated in the same breath because this is where people
overclaim: the six identifier columns are Python `str` objects, which are not
contiguous bytes and cannot be borrowed as a slice. They are converted — one
allocation per string — and `BENCH.md` states explicitly that this cost is **inside
every Rust row in the table, not excused out of it.**

So the honest claim is "zero-copy for the numeric columns," and that is how the docs
word it.

The safety property is enforced by the `'py` lifetime (Q97): the borrowed slice cannot
outlive the Python scope. And `test_zero_copy_does_not_mutate_input_array` asserts the
other direction — that borrowing does not let Rust write back into the caller's array,
which would be a genuinely nasty class of bug.

**Q101. Is the GIL held during scoring?**

No — it is released for the core loop, and that is a single line:

    let scores = py.detach(|| self.core.score_batch(&events));

`py.detach` (PyO3 0.28's name for what older versions called `allow_threads`) releases
the GIL for the duration of the closure. Inside it, the Rust core touches only Rust
data — the `Vec<Event>` already converted — so no Python object is accessed while the
GIL is not held. That is the invariant the API enforces by type: the closure cannot
capture the `Python` token.

The conversion phase **before** it does hold the GIL, necessarily, because it is
reading Python strings.

What this buys in practice, honestly assessed: nothing for `make eval`, which is
single-threaded. It matters for the deployment shape — a server scoring on multiple
threads could run cores concurrently rather than serialising on the interpreter. Since
**no multi-threaded benchmark exists in this repo**, the correct statement is "the GIL
is released so concurrency is possible," not "concurrency is measured." **UNANSWERED:
multi-threaded throughput was never measured.**

**Q102. Why is the Rust engine's memory 2× the Python one? That seems backwards.**

It does seem backwards, and the two causes are both identified and neither is
mysterious (`BENCH.md` §4, `FAILURE_MODES.md` §7).

**Ring pre-allocation.** `Window::new` pre-allocates 64 slots per entity. A churn
entity that only ever sees one event still pays for 64 slots of roughly 72 bytes.
Under high-cardinality churn — where most entities are seen once — that is nearly all
waste.

**Owned strings per slot.** Each retained slot owns two heap `String`s (card,
merchant). Python's tuples hold **references** to string objects that already exist in
the interpreter, so the same logical data costs a pointer rather than a heap
allocation with its own capacity and header.

Measured under the worst realistic case (nearly every event brings a never-seen IP and
device, which carrier-grade NAT genuinely produces): 400,000 events, 531,282 entities,
Rust 2,058 MB and 3,874 bytes/entity, Python 1,049 MB and 1,975 bytes/entity.

Both fixes are mechanical — grow the ring from zero, and intern identifiers to `u64`
handles in a per-detector table, which would also speed up every hash lookup. Estimated
to bring Rust at or below Python. **Not done**, and the reason is the one line I would
say out loud: **closing a 2× constant on an unbounded curve is polish, not the fix.**
The O(entities) growth is the actual deployment blocker and neither fix touches it.

**Q103. Why is floating-point addition not associative, and why did that matter?**

Because every operation rounds to the nearest representable double. `(a+b)+c` rounds
after `a+b`, then again after adding `c`; `a+(b+c)` rounds in a different order. The
two can differ in the last bit. The classic demonstration is `(1e16 + 1) - 1e16`
giving 0 while `1e16 + (1 - 1)` gives 0 correctly — the small value is annihilated by
the rounding of the large sum.

It mattered because the parity requirement here is **bit-exact**, not "within
tolerance." So the six scoring terms must be summed in the **same order** in both
engines, and the Welford update must fold in the **same order** — increment, delta,
mean, then `m2` against the *new* mean. `baseline.rs` says explicitly that reordering
gives an arithmetically equivalent result differing in the last bits.

Why bit-exact rather than approximate: a last-bit difference is invisible until a
score lands within one ulp of the threshold, and then one engine flags an event the
other does not — on one event in millions, non-reproducibly. That is an
undebuggable class of bug, and specifying bit-exactness makes it impossible rather
than rare.

The insight I would offer: **parity risk is made of spec ambiguity, not arithmetic.**
Identical operation order over IEEE 754 doubles gives identical results, so the work
was writing the order down before any Rust existed.

**Q104. You got bit-exact parity on the first run. Isn't that suspicious?**

It would be, if it had been luck. It was paperwork, and I would rather explain the
mechanism than accept the compliment.

Three things were written down **before any Rust existed**: the window convention
`(t−W, t]` with tie behaviour, the exact Welford and EWMA update order, and the
six-term summation order. Those are the three places two implementations of the same
spec can silently diverge. The parity fixture itself was committed first — commit
`fc1f3e4`, "Phase 3: parity fixture, written before any Rust."

Given identical operation order over IEEE 754 doubles, identical results are not
luck; they are arithmetic. The escape hatch in the plan — permission to fall back to
a tolerance if bit-exactness proved impractical — went unused, and **the correct
reading of that is that the paperwork was the work**, not that the risk was
overstated.

And the honest sting in the tail: the first fixture was still **under-covering**. Peak
ring occupancy was 58 of 512 and zero entities ever saturated, so "bit-exact" meant
"bit-exact on the happy path" until review forced a 900-event mega-burst into it
(Q133). The parity number never changed — still 0e0 — but the claim behind it got
much stronger.

**Q105. What is SIMD, and why was it cut?**

SIMD — single instruction, multiple data — applies one operation to several values at
once using wide registers. It is the standard next step when you want more arithmetic
throughput.

It was cut by **decision, not by schedule**, and `Cargo.toml` says so in a comment
next to the empty dependency list: SIMD crates, custom hash maps, custom allocators
and rayon are all excluded deliberately.

Three reasons it would not have helped. The work is **not arithmetic-bound** — the
per-event cost is dominated by hash lookups across four axes, ring pushes and
evictions, and string handling, none of which vectorises. The stream is **inherently
sequential**: each event's score depends on state that the previous events built, so
there is no independent batch of lanes to fill. And it would have put **parity at
risk** for no measured benefit — vectorised floating-point reductions change summation
order, which is exactly what bit-exact parity forbids.

The general principle I would state: at 24.8µs p99 with a 5.5× margin already
achieved, more arithmetic throughput was not the constraint. Optimising the thing
that is not the bottleneck, at the cost of the property the project's credibility
rests on, would have been a bad trade.

**Q106. What does the engine swap prove, and could it have failed?**

It proves the fixture was not under-covering, and yes — it was expected to be able to
fail, which is what makes it a test rather than a ceremony.

`make eval ENGINE=python` and `ENGINE=rust` run the full evaluation — threshold
selection, frontier, ablations, three-seed matrix, every metric — over the full
1.61M-event stream. The two metrics JSONs differ in exactly one line:

    11c11
    <   "engine": "python",
    >   "engine": "rust",

The reason it could have failed: the Phase 3 fixture is 3,866 events, and this run is
**1.6M events at full state depth across three generated streams and three detector
variants**. Entity counts, ring saturation, eviction interleavings and baseline
sample counts all reach regimes the fixture never touches. A divergence here would
have meant the fixture was still under-covering — useful either way.

There was none. Wall time 12m15s Python against 6m47s Rust, most of which is stream
generation and the sweep, which are engine-independent.

This is the headline result of Phase 4 and I would present it as a real parity test
rather than a formality, because a smaller fixture passing is much weaker evidence
than 1.6M events agreeing byte-for-byte.

**Q107. Walk the benchmark numbers and the correction behind them.**

Streaming, which is the shape that matters for an inline control: **Python 21,766
events/s with p50 43.8µs and p99 119.3µs; Rust 120,053 events/s with p50 8.1µs and
p99 24.8µs.** 5.5× throughput, 4.8× better tail.

Batch scoring at six batch sizes: Rust wins every one, from 1.5× at batch=1 (18.90µs
vs 28.55µs per event) to about 4.8× at mid sizes (5.55µs vs 25.22µs at batch=1,000).

**The table is a correction, and the first published version was wrong in both
directions.** The original benchmark re-scored the *same* chunk every repetition, so
batch=1 was measuring re-scoring one hot event — one warm entity, cache-resident
dicts, nothing evicting — and **under-measured** Python by about 2× (13.8µs same-chunk
against 29.8µs on fresh events). The same flaw **inflated** Rust's mid-size rows,
because repeated chunks stacked duplicate events into windows and made them
artificially long (8.6µs → 5.6µs corrected).

The tell was inside the table: batch=1 came out faster per event than batch=100k,
which makes no sense, and the streaming benchmark — which walks fresh events —
disagreed. Each repetition now walks a fresh disjoint chunk.

The correction cuts **against** Rust at batch=1 in relative terms and **for** it at
mid sizes. It was made because the number was wrong, not because of which way it
pointed. That is instance four of the plausible-number pattern (Q131).
---

# 8. TOOLING AND ENGINEERING PRACTICE

**Q108. What is a Makefile, and what does each target here do?**

A Makefile declares named targets and the commands that build them. `make eval` runs
the recipe under `eval:`. Originally it tracked file timestamps to rebuild only what
changed; here it is used as a **task interface** — one memorable name per operation,
so nobody has to remember a long command line, and everyone runs the same one.

The targets:

- **`setup`** — pip install the package editable with dev extras, then `maturin
  develop --release` to build the Rust extension.
- **`data`** — generate the synthetic streams and print the summary.
- **`eval`** — the full evaluation. Takes `SEEDS` (default 3) and `ENGINE` (default
  python), which is how `make eval ENGINE=rust` works.
- **`test`** — `pytest -q`.
- **`demo`** — `spandan replay` over 20,000 test events.
- **`bench`** — the engine benchmarks.
- **`all`** — `data test eval demo`, the deterministic chain.

Two deliberate choices. **Recipes are one command per line with no shell operators**,
so they behave identically under cmd.exe, PowerShell and bash — this project was
built on Windows and that matters. And **`bench` is deliberately not in `all`**,
because its numbers are machine-dependent by nature while everything `all` produces
must match the README exactly on any machine.

**Q109. What is cargo?**

Rust's build tool and package manager, in one. `cargo build` compiles, `cargo test`
runs tests, `cargo add` manages dependencies, and `Cargo.toml` is the manifest —
package metadata, dependencies, and build configuration. `Cargo.lock` pins exact
resolved versions so a build is reproducible.

In this project: `cargo test --release` runs 33 tests — 29 unit and property tests
across the five modules, plus the 4-test parity integration suite declared as
`[[test]] name = "parity"`.

Two manifest details carry weight. `crate-type = ["cdylib", "rlib"]`: **cdylib**
produces the C-compatible dynamic library that Python loads, **rlib** keeps it usable
as a normal Rust library so `cargo test` can link it. And the dependency list is three
entries — `pyo3`, `numpy`, and `proptest` as a dev-dependency — with a comment
recording that SIMD crates, custom hash maps, custom allocators and rayon were cut by
decision rather than by schedule.

**Q110. What does maturin do?**

It builds Python wheels from Rust code — the bridge between cargo and Python
packaging. `maturin develop --release` compiles the crate and installs it into the
active virtualenv as an importable module; `maturin build` produces a distributable
wheel. It is declared as the build backend in `pyproject.toml`, so `pip install -e .`
invokes it through PEP 517.

The configuration is the part worth knowing, because it cost real time
(`BUILD_LOG.md` entry one): `python-source = "python"` says the Python packages live
under `python/`, and `module-name = "spandan_core._native"` puts the compiled
extension **inside** a package directory rather than at top level.

That last point is the gotcha. In maturin's mixed Rust/Python layout the extension
must live inside a package under `python-source`; a top-level `module-name` with no
matching directory is not a valid arrangement, and the error message points at the
missing directory rather than at the concept. My first diagnosis — that `abi3-py310`
required a 3.10 interpreter to be present — was wrong, and the log records the wrong
diagnosis next to the right one.

**Q111. What is a virtualenv, and why did the fresh-clone check need one inside the
clone?**

A virtualenv is an isolated Python environment: its own `site-packages`, its own
installed distributions, so two projects can hold conflicting versions and neither
pollutes the system interpreter.

Why **inside the clone** specifically — and this is a criterion I would defend
firmly, because it earned its place within one run. The failure it catches is not a
stray venv on my machine. It is **`make all` passing because the global
site-packages happens to hold something the clone never declares in
`pyproject.toml`.**

That is exactly what happened. The first fresh-clone run **failed**:
`test_record_mode_without_key_still_never_reaches_the_network` died inside `import
ssl` with `TypeError: function() argument 'code' must be code, not str`. The cause
was an import-order dependency — Python's `ssl` module subclasses `socket.socket` at
class-definition time, so the process's first `import ssl` must happen before my
socket-blocking fixture replaces `socket.socket` with a plain function. On my machine
globally installed pytest plugins (langsmith → httpx → ssl) imported `ssl` at startup
and hid the dependency for weeks. The clone's clean venv declared none of them.

Reproduced on my machine with one variable — `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` —
fixed by importing `urllib.request` at module top with a comment marking it
load-bearing, committed as `bbce950`, and logged as `BUILD_LOG.md` entry nine.

**Q112. What are pytest fixtures, and which ones matter here?**

A fixture is a function that supplies test dependencies — data, a temp directory, a
patched environment — with pytest handling setup and teardown. Declaring a fixture
name as a test argument gets it injected. `autouse=True` applies it to every test in
scope without being requested.

The one that matters here is `no_network` in `tests/test_llm.py`: autouse, so **every
test in that module runs with socket creation disabled** — `monkeypatch.setattr(socket,
"socket", _refuse)` — plus `GEMINI_API_KEY` and `SPANDAN_LLM_MODE` deleted from the
environment.

That converts a documentation claim into a physical property. "Replay mode never
touches the network" would otherwise be a docstring I am asking a reviewer to trust;
with the fixture, any attempt to open a socket raises immediately. The claim is
enforced at the OS boundary rather than rhetorically, and `monkeypatch` guarantees the
patch is reverted afterwards regardless of outcome.

Other fixtures: `built` in `test_gen.py` builds a stream once per module rather than
per test, and `tmp_path` gives each test its own directory so builds cannot collide.

**Q113. What is a cassette in the record/replay sense?**

A recorded response, replayed later instead of making the real call. Standard practice
for testing code that talks to an external service: record once, commit the recording,
and every subsequent run is fast, deterministic, offline, and free.

Here (`llm/provider.py`): each cassette is a JSON file keyed by
`sha256(model + "\n" + prompt)[:32]`, holding the key, the model id, the prompt, the
response text, and a `recorded_via` field naming exactly how it came to exist. Two are
committed.

Three design decisions I would defend. **Replay is the default**, so the network path
is opt-in via `SPANDAN_LLM_MODE=record`. **A cassette miss raises `CassetteMiss`
loudly and never falls through to the network** — because a "replay" mode that quietly
records is how an offline test suite starts costing money and leaking prompts. And the
key includes the **model id**, so changing models produces new keys rather than
silently replaying another model's answer as though it were this one's.

`test_cassettes_declare_their_provenance` asserts every cassette states its true
origin — a plausible artifact with an untrue origin would be another instance of the
project's own failure pattern.

**Q114. Why does determinism matter for the LLM layer specifically?**

Because a test suite that calls a language model is not a test suite. Three problems,
all fatal.

**Non-determinism**: the same prompt returns different text, so any assertion stronger
than "a string came back" fails intermittently. **Cost and rate limits**: a full suite
run becomes an API bill and a source of flakes. **Network dependency**: `make test`
stops working offline, in CI, and in a fresh clone — and the fresh-clone reproduction
is a hard acceptance criterion of this project.

There is a fourth reason specific to the claim this project makes. The central hygiene
claim is that **no number in the evaluation passed through a language model**. If the
test suite itself made network calls, the audit trail would be muddier: someone would
have to check *which* calls were which. With replay as the default and sockets blocked
in tests, the claim is checkable in one line.

The cassettes also make the fabrication finding (§9) reproducible: the flawed output
is committed byte-for-byte, so anyone can read exactly what the model said rather than
taking my summary of it.

**Q115. What does `git revert` do versus `git reset`?**

**`git revert <commit>`** creates a **new commit** that undoes the changes of an
earlier one. History is preserved — the bad commit and its reversal both remain
visible. Safe on shared branches, because nobody's history is rewritten.

**`git reset`** moves the branch pointer to a different commit, discarding the ones
after it from that branch's history. `--soft` keeps changes staged, `--mixed`
(default) unstages them, `--hard` **discards them from the working tree entirely**.
Rewriting published history means anyone who pulled has diverged.

The rule I worked under, and applied: **never rewrite history; correct forward.**
When commit `fc1f3e4` accidentally took extra files, the fix was a forward correction,
not a rewrite — partly because the working environment auto-pushes on commit, so
history was already published by the time the mistake was visible.

Related rule from the same incident: **no `git add -A`.** A blanket add once swept an
unrelated file into a commit (`2acc9fd` untracks it). Explicit staging only — every
commit in this project names its files.

**Q116. What would CI have caught here, and why is there none?**

There is no CI in this repository, and the honest answer to why is: an eleven-day
solo build where the same machine ran every check, and setting up a matrix looked like
schedule I did not have. That is a reason, not a justification.

What it would have caught is specific, not hypothetical: **the import-order bug in
Q111.** A CI runner is a fresh environment with only declared dependencies by
construction — exactly what my machine was not. The bug survived for days because
globally installed pytest plugins imported `ssl` before my fixture ran. CI would have
failed on the first push.

What the fresh-clone acceptance criterion does instead is approximate CI **once,
manually, at the end**: clone, venv inside the clone, `make setup && make all`, tests,
and `git status --porcelain` must print nothing. It caught the bug — which is evidence
both that the criterion works and that having it run continuously would have been
better.

The second thing CI would give me is a **platform matrix**. Every number in `BENCH.md`
is from one Windows machine, and I have no Linux or macOS measurement at all.
**UNANSWERED: cross-platform behaviour and performance are entirely unmeasured.**

**Q117. What are your `.gitignore` rules protecting against?**

Four classes, and two of the entries are more considered than they look.

**Generated data** — `/data/`, root-anchored deliberately. A bare `data/` would also
match any nested directory, and `tests/fixtures/parity.json` **must** be committed,
because a parity fixture regenerated at test time proves nothing about drift.

**Build outputs** — `/spandan-core/target/`, `__pycache__/`, `*.egg-info/`,
`.pytest_cache/`, `build/`, `dist/`.

**The compiled extension and its debug symbols** — `*.pyd`, `*.so`, `*.dylib`,
`*.pdb`. A committed binary is unreviewable, platform-specific, and stale the moment
the source changes. The `.pdb` is a specific instance: a debug-symbol file that
appeared in the working tree during Phase 0 and would have been committed silently.

**Virtualenvs** — `.venv/`, `venv/`.

Plus one honest entry: `docs/*.md.bak`, stray duplicates left on disk rather than
deleted, with a comment saying they remain in `docs/` pending a decision.

The backstop that matters more than any individual rule is the acceptance criterion:
**`git status --porcelain` must print nothing after `make all`.** That catches the
whole class of build-artifact leakage rather than one file at a time.

**Q118. Why is `.gitattributes` set to `text=auto eol=lf`?**

It normalises line endings: text files are stored in the repository with LF regardless
of what the working tree uses, and `*.png`/`*.pyd` are marked binary so git never
tries to translate them.

It matters here for a specific reason beyond tidiness. This project was built on
Windows, where the default is CRLF in the working tree, and it ships a **committed
parity fixture** that both a Python test and a Rust test read. Inconsistent line
endings across clones would change file bytes, which would break the
byte-identical-build assertion in `test_seed_reproducible_byte_identical` and could
break the TSV parity loader's parsing.

You can see it working in the commit output — git prints "CRLF will be replaced by LF
the next time Git touches it" on every doc commit. That warning is the normalisation
doing its job, not an error.

**Q119. What is the difference between `pytest -q` and the full run, and what does
your suite cost?**

`-q` is quiet mode — dots instead of a line per test. The `addopts = "-ra"` in
`pyproject.toml` adds a summary of all non-passing outcomes at the end, so short
output never hides a skip or an xfail.

The suite is **95 tests**: 34 eval, 22 generator, 20 reference detector, 10 LLM, 6
parity, 3 smoke. It takes **about 11–14 minutes** — 676s inside the fresh clone's
`make all`, 868s on the standalone re-run.

That is slow for a unit suite and the reason is honest: several tests **build a real
synthetic stream and run the full evaluation over it**. `test_eval_runs_with_llm_import_poisoned`
alone runs the entire evaluation twice, poisoned and clean, because a cheaper version
of that test would not prove the thing it exists to prove.

The trade I made deliberately: property tests and end-to-end tests that exercise the
real pipeline, at the cost of a suite nobody runs casually. In a longer project I
would split it into a fast tier and a slow tier so the fast one could run on every
save — the current arrangement discourages running tests, which is its own risk.

**Q120. How do you know the fresh clone reproduces the README's numbers?**

Because it was run, and the numbers were checked line by line rather than assumed.

The procedure: `git clone` to a fresh directory, create a venv **inside the clone**,
verify the interpreter is the clone's own (`sys.executable` printed and inspected),
`make setup && make all`, then `pytest -q`, `cargo test`, and `git status
--porcelain`.

The result on the second run: 95 passed inside `make all` and 95 passed again on the
standalone run, 29 + 4 Rust tests passing, porcelain **empty**, script exit code 0.
And the eval output in the clone reproduced precision 0.0824 at the 0.15% base rate,
0.4462 at the generator's rate, 1-in-71 legitimate declines, 487 alerts, break-even
₹613, threshold 21.99, and 60/60 episodes — the same figures the README quotes.

The first run failed, which is the more useful half of the story (Q111).

One process detail I would confess: my own check script initially piped output through
`tail`, so it reported the pipeline's exit code rather than make's, and the first
failure showed as exit 0. An unchecked pipeline exit is the same failure class as an
unchecked Win32 return — it is recorded in `BUILD_LOG.md` entry nine as a bonus
instance of the project's own pattern.
---

# 9. THE LLM LAYER

**Q121. Why does an LLM layer exist in this project at all?**

To turn a flag into something an analyst can act on in five seconds, and to
demonstrate a boundary.

The task is exactly one function: `explain_flag(flag) -> str`. It takes the frozen
`Flag`'s fields and returns a triage note — what the traffic looked like, the rupee
exposure, **what would make this a false positive**, and one next action. The
dismissal test is the valuable part: an analyst's fastest action is recognising the
benign explanation.

The scope was pinned hard and held: one task, one provider, cassettes committed, no
tool use, no second task, no agent loop.

But the honest framing after the results are in is different from the framing going
in. **What this layer actually earned its place with is the boundary, not the prose.**
The recorded model output fabricated evidence (Q125), the deterministic template
ships instead, and what survives as a contribution is the pair of tests proving that
no number in the evaluation can pass through a language model. `TARGET.md` says it in
those words: the poisoned-import test is worth more than any prose above it.

**Q122. Why can the LLM not decide a flag? Prove it.**

Four mechanisms, three of which are enforced by tests rather than by discipline.

**The import graph.** `test_detect_and_eval_import_graphs_exclude_spandan_llm`
imports `spandan.detect` and `spandan.eval` and every submodule, then asserts nothing
matching `spandan.llm` appears in `sys.modules`. If either package ever gains a path
to the LLM layer, this fails on the first edge.

**The poisoned-import test.** `test_eval_runs_with_llm_import_poisoned` replaces
`sys.modules['spandan.llm']` and its submodules with an object that raises on **any**
attribute access, then runs the full evaluation — scoring, the 600-point sweep,
constrained selection — twice, poisoned and clean, asserting `np.array_equal` on both
score arrays and an identical selected threshold. It also asserts the poison actually
bites, so a broken poison cannot pass silently.

**The type system.** `Flag` is frozen, so even a hostile explanation cannot write back
into the evidence; `explain_flag` returns `str` and nothing else.

**The data flow.** The prompt is assembled only from `Flag` fields —
`test_prompt_contains_only_flag_fields_no_labels` checks that no label, scenario id, or
scenario name appears in it.

The claim that follows is stronger than a promise: **not "we didn't", but "we
structurally could not have."**

**Q123. What is the provider abstraction and why a single egress point?**

`llm/provider.py` exposes one function — `complete(prompt, model) -> str` — and it is
the only place in the repository that may open a connection to a model provider.

Two modes on `SPANDAN_LLM_MODE`. **replay** (default): answers come from committed
cassettes keyed by `sha256(model + "\n" + prompt)[:32]`; no network, no key, no
sockets; a miss raises `CassetteMiss` loudly and never falls through. **record**: one
HTTPS call per cache miss to Gemini's OpenAI-compatible chat-completions endpoint,
model `gemini-3.1-flash-lite`, authenticated by `GEMINI_API_KEY` read straight from
the environment — no .env file and no dotenv loader, so there is nothing to
accidentally commit.

A single egress point buys three things: it is one file to audit for the "does this
ever call out" question; it is one place to change providers, which I did twice
without touching any other module; and it is what makes the import-graph test
meaningful, because there is exactly one thing to keep unreachable.

The HTTP call uses `urllib`, deliberately not a provider SDK, so the layer adds **no
runtime dependency** — `pyproject.toml`'s dependency list is still just numpy. And the
error path reads the API's JSON body on `HTTPError`, because a bare "402 Payment
Required" with the explanation swallowed cost real debugging time.

**Q124. Walk the record/replay flow and what the cassette holds.**

`complete(prompt, model)` hashes model plus prompt into a 32-character key and looks
for `cassettes/<key>.json`. If it exists, the recorded `response_text` is returned —
no network, regardless of mode. If not: in replay mode it raises `CassetteMiss` with
the key and the instruction for recording deliberately; in record mode it makes the
call, writes the cassette, and returns the text.

Each cassette holds the key, the model id, the full prompt, the response text, and
`recorded_via` — which for the committed pair reads `gemini openai-compatible
chat-completions api, model gemini-3.1-flash-lite, urllib, SPANDAN_LLM_MODE=record`.

The provenance field is enforced, not decorative:
`test_cassettes_declare_their_provenance` requires it present, over 40 characters, and
requires the `key` field to match the filename. That test exists because of a specific
temptation resisted: before an API key was available, the first cassettes were written
in-context by the model building the project, and their `recorded_via` said exactly
that rather than claiming a wire recording that never happened. **A plausible artifact
with an untrue origin would have been another instance of this project's own failure
pattern** — the most on-brand possible way to lose credibility.

Those in-context cassettes were superseded by real recordings and live in git history
(`8a6bf8d`).

**Q125. Tell me about the fabrication finding, in detail.**

The recorded model output invented fields that do not exist, and instructed the
analyst to act on them.

On the ₹5.45 probe (cassette `9738bd8f…`), the model's entire decision rule was:
*"Block the BIN for 24 hours if the CVV/AVS result on this attempt returned
'Mismatched' or 'Not Supported.'"* **There is no CVV or AVS field** in the `Flag`, in
the prompt, or anywhere in this pipeline. On the ₹150 case (`7e36f73e…`) it ordered:
*"Review the card's transaction history. If no successful prior history exists at this
merchant, blacklist the card and cardholder IP immediately."* Per-card history and
cardholder IP are equally absent.

Both next-actions are therefore conditioned on data the analyst does not have. That is
the worst failure shape for a triage note — not vagueness but **confident, specific,
ungrounded grounds for a block.**

Second failure: both notes narrate a single-credential causal story — "a bot testing a
single stolen credential" — when the detector fires on velocity and decline-ratio
deviation across an entity's window against learned baselines. One note attributes the
BIN's baseline ticket to "the merchant's average." An explanation that misstates what
was measured teaches the analyst a wrong model of the alarm.

And the aggravating detail: **the prompt explicitly said "the evidence below is
everything known."** It fabricated anyway. Prompt discipline is not a boundary — which
is precisely why this project built a structural one.

**Q126. Why did you keep the bad output instead of re-prompting?**

Because re-prompting until a nicer sample appears and shipping that one is **the same
error as selecting a threshold on the test set.** I would use exactly that sentence.

The whole project's credibility rests on not choosing what to report after seeing the
results. I refused to move the alert budget after seeing that budgets 2 and 5 scored
better on test. Quietly regenerating an LLM explanation because the first one was
embarrassing would be the identical move in a place where it is easier to get away
with — nobody would ever know there had been a first attempt.

There is also a methodological point. The comparison protocol was fixed in advance: a
hand-written target explanation was committed **before any LLM code existed** (commit
`3676a82`), specifically so the bar could not be written to fit the output. Re-rolling
the output after seeing it fail the bar would have destroyed the only property that
made the comparison meaningful.

So the cassettes are committed exactly as returned, `TARGET.md` carries the verdict
that the model output is **not better** than the hand-written target and why, and
`FAILURE_MODES.md` §8 makes the architectural argument. The flawed output **is** the
finding.

**Q127. So the LLM layer failed. Why is it still in the submission?**

Because a measured negative result with a bounded blast radius is a contribution, and
deleting it would be hiding a result.

What the layer demonstrates now is the thing that actually matters: **a hallucinating
explainer degrades one analyst note and cannot corrupt a number**, and that is
structural rather than lucky. The poisoned-import test proves the evaluation runs
bit-identically with `spandan.llm` unimportable. The frozen `Flag` means the
explanation has no write path. The single egress point means there is one thing to
audit.

The counterfactual is the argument. **Had the explainer been allowed near the decision
path — the "LLM triage" shape that is currently fashionable — that fabricated CVV/AVS
rule would have been a fabricated *blocking criterion*.** Here it degrades one note, is
caught by reading the note against the schema, and corrupts nothing.

What ships as the explainer is `render_template` — the hand-written target reduced to
what `str.format` can fill. It cannot fabricate, because substitution can only place
fields that exist. On this evidence that property is worth more than fluency.

The unbuilt fix is recorded in §8: a post-hoc validator checking every factual clause
in a model note against the `Flag`'s fields. Not built, because the template already
has that property for free.

---

# 10. THE PLAUSIBLE-NUMBER PATTERN

**Q128. Instance one: precision 1.00.**

**The number:** early per-scenario evaluation reported precision of 1.00 — every
flagged event a true positive.

**Why it looked fine:** the pipeline was correct. Nothing was broken; the detector
genuinely achieved that on the stream it was measured on.

**What was actually wrong:** the test window held **two attack episodes per
scenario**. A perfect score on a sample of two is not a measurement. Per-scenario
recall was swinging **0.16–0.54** across seeds for the same reason.

**What caught it:** the multi-seed check. Running three independently generated
streams turned one impressive number into a range that could not support the claim.

**The fix:** the stream went from 14 days to 100, and episodes from 2 to 20 per
scenario per split — with the **episode rate per day held constant**, so difficulty
did not change. `test_more_episodes_came_from_a_longer_stream_not_a_denser_one`
asserts the density is unchanged. Recall's spread fell from 0.386 to 0.092.

**Q129. Instance two: bit-exact parity on a fixture that never filled a ring buffer.**

**The number:** maximum score delta between the Rust core and the Python reference of
exactly **0e0** against a tolerance of 1e-9. Bit-exact on the first run.

**Why it looked fine:** it is the best possible result on the hardest-sounding
criterion, and the mechanism was real — the window convention and summation order had
been written down before any Rust existed.

**What was actually wrong:** the fixture never exercised the interesting paths. Peak
ring occupancy was **58 of 512** and **zero** entities ever saturated. So "bit-exact"
meant "bit-exact on the happy path" — the eviction-under-saturation interaction, the
most likely place for two implementations to diverge, was untested.

**What caught it:** a review instruction to *measure the coverage* rather than trust
the result. Nobody could have seen this by looking at 0e0.

**The fix:** a 900-events-in-4-minutes mega-burst added to `PARITY_CONFIG`, saturating
all four axes, with merchant and BIN drain afterwards so eviction-after-saturation is
covered too. Guards pinned on both sides — `test_parity_fixture_saturates_the_ring_buffers`
in Python and `parity_fixture_saturates_the_ring_buffers` in Rust. Delta still 0e0,
but now it means something.

**Q130. Instance three: 0.0MB RSS.**

**The number:** every RSS column in the first `make bench` run read **0.0MB** — peak
RSS "0 MB", full-stream growth "0.0MB" for both engines.

**Why it looked fine:** the throughput tables around it were correct, so the output as
a whole looked credible. As `BUILD_LOG.md` puts it, a zero inside a table whose other
columns are honest is **the most credible possible disguise**. And "Windows memory
accounting is awkward from Python" is a plausible story.

**What was actually wrong:** two stacked ctypes mistakes.
`ctypes.windll.psapi.GetProcessMemoryInfo` resolves on some Windows builds and not
others — modern Windows exports it as `K32GetProcessMemoryInfo` on kernel32 — and with
no declared `argtypes`, the pseudo-handle from `GetCurrentProcess()` went through a
default `c_int` and was truncated on 64-bit. The call failed, **the return value went
unchecked**, and the zero-initialised struct was read as data.

**What caught it:** checking the return value. Not inspecting the number — checking
whether the call succeeded.

**The fix:** `K32GetProcessMemoryInfo` via `WinDLL("kernel32")` with declared
`argtypes`/`restype`, `HANDLE` restype on `GetCurrentProcess`, and the return checked
— a failure now raises "RSS figures would be fiction" rather than reporting zeros. The
re-run exposed a real finding the zeros were hiding: **Rust costs 2× the memory per
entity.**

**The lesson in one line:** a measurement that fails soft is worse than no
measurement.

**Q131. Instance four: the batch=1 benchmark.**

**The number:** 40,816 events/s of per-event Python scoring — a strong, credible
baseline.

**Why it looked fine:** ~25µs per event over warm dicts is not implausible for
CPython. And note the **incentive gradient**, which is the interesting part: a slower
honest Python baseline makes Rust look *better*, so this error was flattering the
comparison's loser. **Nobody inside the project had a motive to catch it.**

**What was actually wrong:** the benchmark re-scored the **same chunk** every
repetition. At batch=1 that measures re-scoring one hot event against one warm entity
— cache-resident dicts, nothing evicting — under-measuring fresh-event work by about
2× (13.8µs same-chunk against 29.8µs fresh, verified directly). The same flaw
*inflated* Rust's mid-size rows, because repeated chunks stacked duplicate events into
windows.

**What caught it:** a review instruction applying the project's own standard in the
uncomfortable direction — **an unexpectedly good baseline number deserves the same
suspicion as an unexpectedly good precision number.** The tell was inside the table:
batch=1 came out faster per event than batch=100k, and the streaming benchmark
disagreed.

**The fix:** every repetition walks a fresh disjoint chunk. The corrected table is
published with the original marked wrong **in both directions**, because it was.

**Q132. Instance five: the patch script's empty grep.**

**The number:** an empty verification result, treated as success.

**What happened:** a script applying edits to documentation wrote to the **wrong path
variable** — the content intended for one file landed on another's working copy. The
script then ran a verification grep to confirm the change, the grep returned **empty**,
and the empty result went unchecked.

**Why it looked fine:** the script ran, printed no error, and exited 0.

**What caught it:** the editor's file-change notices — an external signal, not the
tooling's own verification.

**The fix:** patch scripts now **assert every replacement** and verify each file is
itself afterwards.

**Why it belongs in this list even though it is tooling rather than a measurement:**
it is the **same failure class as the unchecked Win32 return** — a check that can
return "nothing" and a caller that treats "nothing" as "fine." Same class, one layer
down, in the tooling that writes the documents the measurements go into.

**Q133. Instances six and seven, since the list has grown.**

The pattern kept producing instances after the README's list of five was written, and
both are worth having ready.

**Six — the fabricated explanation.** A well-formed analyst note citing CVV/AVS
results and per-card history that exist nowhere in the schema (Q125). It passed every
format check — four parts, under 140 words, no score restated. Caught by reading the
prose **against the schema**, which is the same move as asking whether a number should
be true, applied to text. `BUILD_LOG.md` entry eight; the lesson recorded there is
*prompt discipline is not a boundary; the import graph is.*

**Seven — my own check script's exit code.** The fresh-clone verification piped its
output through `tail`, so the pipeline reported **tail's** exit status rather than
make's. The first fresh-clone run **failed** and was reported as exit 0. Caught by
reading the actual output rather than trusting the status line. Same class as the
unchecked Win32 return and the unchecked grep — a status that can be wrong, and a
caller that trusts it. Recorded in `BUILD_LOG.md` entry nine.

That instance seven is *mine, in the verification harness, after writing five entries
about exactly this* is the most honest thing I can say about how persistent the
pattern is.

**Q134. Tie them together. What is the general claim?**

The claim is about **mechanism, not carefulness**, and the sentence I would say
verbatim is:

> **None of these were caught by staring harder at the output — each was caught by a
> guard, a re-run under different conditions, or someone asking whether the number
> should be true.**

Map it: precision 1.00 fell to a **re-run under different conditions** (multi-seed).
The parity fixture fell to **measuring the coverage** rather than reading the result.
0.0MB RSS fell to **checking a return value**. The batch benchmark fell to **someone
asking whether an unexpectedly good number should be true**. The patch script and my
own check script fell to **external signals**, because their internal verification was
the thing that was broken.

The reason this matters in an interview: **"I'm careful" is what every candidate
says**, and it is unfalsifiable. Every one of these errors was made by someone being
careful. What distinguishes the project is that the failures were caught by machinery
that does not depend on attention — guards that fail loudly, tests that assert
properties rather than mirror code, re-runs under changed conditions, and a review
habit of asking whether a good number *should* be good.

The two retractions — the EWMA ablation and the coarse-grid improvement — are the same
habit applied to results rather than to bugs. And the failure class is unified:
**a plausible artifact with nothing behind it.**
---

# 11. JUDGMENT AND SELF-CRITIQUE

**Q135. What is the weakest part of this project?**

**There is no learned baseline to compare against.** No logistic regression, no
gradient-boosted model, nothing. So the claim implicit in the whole design — that six
hand-weighted terms over streaming baselines are a reasonable detector — is
**untested**. A panel could reasonably say a 20-line scikit-learn model on the same
features might beat it, and I could not answer with a number.

It is also the cheapest missing experiment in the project. The data exists, the
splits exist, the metrics exist, `FEATURE_COLUMNS` is already defined and excludes
ground truth. It would take an afternoon.

Why it is not there, honestly: the eleven days went into the evaluation apparatus, the
Rust port, and the failure analysis, and every phase gate was spent on making the
existing numbers trustworthy rather than adding new ones. That is a defensible
prioritisation — the evaluation is what makes *any* detector's numbers believable — but
it is a prioritisation, not a justification, and the gap is real.

The second weakest is closely related: **the six weights were never sensitivity-tested
either**. I cannot tell you how much the result would change if `w_decline_bin` were
1.5 instead of 2.0.

**Q136. What is your worst number, and how do you state it?**

**50.5% of a legitimate single-merchant issuer outage is flagged as card testing** —
9,170 of 18,169 events — with headroom of −267%: the highest-scoring clean event
scores **80.79** against a threshold of 21.99, on every seed.

How I state it: not as a rate of mistakes but as a **named blind spot**. The detector
cannot distinguish legitimate traffic whose declines are concentrated on one BIN at
one merchant from card testing, because the one feature that would — the same card
retried — is invisible at a 5-minute window. §2.2 works out the mechanism: 1.13
in-window attempts per card for an outage against a burst's 1.22, so **the damping term
points the wrong way.**

Then the framing that matters: **the control was built to find exactly this, and it
found it.** A negative-headroom result from a purpose-built control is the control
working. And it is a sharper statement than any precision figure — not "it makes
mistakes at this rate" but "there is a specific, common, nameable class of legitimate
traffic it cannot see the difference from."

The last thing I add is the warning about the rupee column: that failure costs only
₹8,595 in the cost model, because outage traffic was declining anyway. **Do not read
the low cost as evidence the failure is unimportant** — that is a statement about the
cost model.

**Q137. What would you build next, and why is it not built?**

**A second, much longer window on the BIN axis** — recommendation 1 in
`FAILURE_MODES.md` §7 — and it is not built because of a freeze I would defend.

Why it is first: it addresses the worst failure directly. Retry structure separates an
outage from a probe run **over a whole episode** (4.7 attempts per card against 1.0)
and is invisible at 5 minutes. A long-horizon window makes the existing damping term
work as designed. It would likely also help `slow_low`, my weakest scenario at 0.445
event recall, for the same reason — 48–66 events over 5.5–7 hours is a shape a
5-minute window cannot see.

Why not built: Phase 3 ports this detector to Rust and tests bit-exact parity against
it, so `reference.py` **is the parity spec**. Changing the reference moves the spec.
Adding a second window to the BIN axis is a redesign of the scoring function, not a
parameter change, and it would have landed in the same 24 hours as the port, with a
pre-committed one-day hard stop on parity work already load-bearing.

So the detector was **frozen from the Phase 2 gate onward** and the fix was recorded as
diagnosed-not-attempted, with the mechanism worked out. I would rather show a panel a
correctly diagnosed unbuilt fix than an undiagnosed rushed one — and the freeze is
what made the bit-exact parity result trustworthy.

**Q138. What would you do differently if you started again?**

**Resolve "what does a flag do" on day one.** It stayed ambiguous through Phase 2 and
the ambiguity was load-bearing: the report constrained the operating point on
alerts/day (analyst workload) while the cost model charged blocked-transaction value
per event (which only makes sense if flags block). Read one way, capping alerts looked
like capping merchant impact — and it is nothing of the sort, since 20,254 flagged
events collapse into 487 alerts. Every number in the evaluation had to be re-read once
that was settled. Settling it first would have shaped the cost model, the constraint,
and the headline from the start.

**Second: build the learned baseline early**, even a bad one, so every subsequent
claim about the hand-weighted design has a comparison attached.

**Third: run the evaluation on more than one machine, or set up CI.** Every benchmark
number is from one Windows box, and the import-order bug that the fresh clone caught
would have been caught on day one by any second environment.

What I would **not** change: the phase gates with written evidence, the negative
controls built to attack my own primary signal, and the rule that superseded figures
get marked rather than swapped. Those are what produced every real finding here.

**Q139. What would a production version need that this lacks?**

Six things, roughly in order of how quickly they would bite.

**Bounded total memory.** Entities are never freed — 31 GB a month projected at 8M
entities. Needs LRU eviction or a count-min sketch, and both trade accuracy: eviction
cold-starts returning entities, the sketch over-counts on collisions, which inflates
velocity evidence in the **unsafe** direction.

**Persisted state.** Every restart re-opens the cold-start failure — 5 false
positives in a 29-flag demo window, all on the outage control.

**Label ingestion and retraining.** Chargebacks arrive weeks late and partly wrong;
nothing here consumes them.

**Drift monitoring and threshold re-selection.** A threshold chosen in June is still
21.99 in December and nothing recomputes it.

**Reason codes and richer features** — reason code, 3DS, AVS/CVV, MCC, geography — the
fields §2.6 excludes and which probably fix the worst failure.

**Operational surfaces**: allow-lists, incident overrides, an analyst feedback loop,
and a kill switch, because an inline control that declines 1 in 71 legitimate
customers needs a way to be turned off in one action during an incident.

And the honest one: **a real-data validation before any of it ships.** These are
synthetic-stream numbers.

**Q140. Where did you get lucky?**

Three places, and I would name them before someone finds them.

**The parity result.** Bit-exact on the first run was earned by writing the window
convention and summation order down first — but the first fixture was still
under-covering (peak occupancy 58 of 512, zero saturation). If the mega-burst had been
added *before* the port and the port had then failed, I would have spent days I did
not have. The order of events was lucky; the paperwork was not.

**The 100-day regeneration landed.** Growing the stream 7× and re-running everything
could easily have produced a mess of shifted numbers mid-schedule. It cost one phase
and improved the evidence.

**Nobody had to debug a real FFI memory bug.** The zero-copy path could have produced
a subtle aliasing or lifetime problem — the class of bug that eats a week. It did not,
partly because the design kept the boundary narrow, partly because PyO3's types
enforce the invariants, and partly because I was not unlucky.

The counterweight worth saying in the same breath: **the schedule had genuine slack
and the cut list was never needed.** That is not luck, it is what the phase plan was
for — but it meant a bad day never compounded.

**Q141. What are you most proud of?**

The retraction, and I would pick it over any result.

Phase 2 claimed dropping the EWMA baseline improved net position by ₹17,159 and that
EWMA "is not carrying the detection signal." Measured on one stream. **That claim was
withdrawn** when three seeds gave a delta of ₹12,981 median with a range of
[−₹43,496, +₹109,443] — it changed sign across streams. And the **opposite** claim was
refused on the same evidence.

Then the part that matters more: when the operating point changed, the measurement was
**run again** rather than the old conclusion carried forward. At the constrained point
both ablations beat the full detector consistently, 3 of 3 seeds — and that still does
not license "EWMA is useless," because net rupees was already demonstrated to be
nearly blind to the axis that matters.

The whole three-pass history is kept verbatim in `FAILURE_MODES.md` §3, including the
withdrawn claim in its original wording.

Why it is the thing I am proudest of: **a number you cannot defend is not a result,
whichever way it points** — and it is much easier to write that sentence than to delete
a finding you liked. The same habit produced the superseded coarse-grid figures and
the kept-as-recorded LLM cassettes.

**Q142. If you had two more weeks, what is the order of work?**

**Days 1–2: the learned baseline.** Logistic regression and a gradient-boosted model
on the same temporal split, the same three precisions, the same rupee model. This
closes the biggest hole in the submission whichever way it comes out, and if the
learned model wins I would say so.

**Days 3–5: the long-horizon BIN window.** The diagnosed fix for the worst failure,
now that the freeze has served its purpose. Re-run the parity fixture and re-port —
which is exactly the work the freeze deferred.

**Days 6–7: the joint flag-rate constraint**, done honestly: state the cap and its
operational basis first, in a commit, **then** measure once.

**Day 8: variance reduction** — common random numbers across ablation variants, so §3
stops being a null result for measurement reasons.

**Days 9–10: memory** — interning and ring right-sizing to close the 2× constant, then
an LRU prototype to attack the actual growth curve.

**Days 11–12: CI and a second platform**, because the import-order bug proved one
machine is not an environment.

**Days 13–14: re-run everything and rewrite the numbers.** Every figure in every
document is regenerated, because a change to the detector invalidates them all — and
half-updated documents are how a project starts lying.

**Q143. What single change would most improve the headline number?**

Almost certainly the **long-horizon BIN window**, and I can be specific about the
mechanism rather than hopeful.

The headline is precision 0.0824 at a realistic base rate. Precision is dominated by
false positives, and the false positives are dominated by one control: 9,170 of the
11,216 false positives come from `outage_single_merchant` alone, against 208 from all
740,349 ordinary benign events and 20 from the flash sale. **Fix that one failure and
the false-positive count falls by an order of magnitude.**

The mechanism is diagnosed: retries separate an outage from a probe over an episode
(4.7 attempts per card against 1.0) and are invisible at 5 minutes (1.13 against
1.22). A longer window on the BIN axis makes the existing `repetition` term work as it
was designed to.

Two caveats I would attach. First, it is a **hypothesis with a mechanism, not a
result** — I did not build it, so I cannot tell you the new precision. Second, it would
change nothing about the base-rate arithmetic: at 0.15% prevalence, precision stays
brutal unless the false-positive rate falls a long way. The honest ceiling statement is
that this change attacks the biggest single term, not that it makes the detector
deployable.

---

# 12. CURVEBALLS

**Q144. "This is just a rules engine with extra steps. Where is the ML?"**

There is no ML, and I would say that in the first sentence rather than let it be
discovered in the fourth.

But "rules engine" is not quite right either, and the distinction is worth one
sentence: a rules engine compares against **fixed constants** a human picked. Every
term here is a deviation from a **baseline the stream teaches it** — `velocity_bin` is
a z-score against that BIN's own EWMA centre and Welford spread, so the same absolute
event count is unremarkable for a busy BIN and extreme for a quiet one. The adaptive
part is unsupervised and per-entity; the fixed part is the combination.

Then the real answer: **the hard part of this problem was never the classifier.** It
was building an evaluation that can tell you whether *any* classifier works — temporal
splits, base-rate-honest precision, a rupee cost model, and negative controls that
attack the detector's own primary signal. That apparatus is what found every real
weakness here, and it is what a learned model would have needed to be trustworthy.

And the concession, unprompted: **I never built the learned comparison**, so "the
hand-weighted design is good enough" is an untested assertion. It is the first thing I
would build with two more weeks, and if a gradient-boosted model beat it I would report
that.

**Q145. "You tested on data you wrote yourself. Why should I believe any of it?"**

You should believe the **engineering** and treat the **performance numbers as
unvalidated**. That is the honest split, and I would offer it before defending
anything.

The constraint was real: no public dataset has BIN, IP and device together, and I can
name the specific disqualifying property of each candidate — IEEE-CIS is not
card-testing-specific and lacks the axes, PaySim is mobile-money with no authorization
concept, the Kaggle set is anonymised PCA components with no interpretable field at
all.

Then three things that make the data harder rather than easier: benign decline rates
of 4.5–11.5% so decline ratio is not a free separator; attack episodes borrowing BINs
that carry real benign traffic, so attacks must deviate from an established baseline;
and card novelty banned outright and enforced by the **type system**, because my
generator's novelty structure would otherwise separate the classes for free.

And the strongest evidence that I am not grading my own homework: **the negative
control built to attack my own primary signal fails, and it is the headline of my
failure-modes document.** 50.5% flagged, headroom −267%. Someone gaming their own
evaluation does not build that control, and certainly does not lead with it.

`ASSUMPTIONS.md` §2 lists ten ways this stream is unlike real traffic and says which
way each distortion pushes. Three of them say my results are flattered.

**Q146. "Why should we believe any of these numbers?"**

Because each one is reproducible from a clean machine and most of them are worse than
the numbers I could have reported.

**Reproducible**: clone, make a venv inside the clone, `make setup && make all`. That
was run — it failed the first time on a real bug (an import-order dependency masked by
my machine's global packages), the bug was fixed as `bbce950`, and the second run
reproduced every README figure exactly with `git status --porcelain` empty.

**Worse than they could have been**: I lead with precision **0.0824** rather than the
0.4462 the same run produces, because 1.33% prevalence is roughly ten times realistic.
I report **1-in-71 legitimate customers declined** rather than hiding behind 9.7
alerts a day. I kept the alert budget at 10 even though budgets 2 and 5 score better
on test, because the budget was registered before the test window was read — commit
`e5b48f8`.

**And the history is visible**: two retractions kept verbatim, superseded figures
marked rather than swapped, and nine build-log entries about my own errors — including
the one where my own verification script reported exit 0 on a failed run.

The claim I am making is not "these numbers are right." It is: **you can check them,
and where I found them wrong I said so in writing.**

**Q147. "Card testing is already a solved problem — velocity rules and 3DS handle it."**

Largely true at a PSP or scheme level, and I would concede it rather than argue.

The strongest defences are structural, not detective: 3DS shifts liability and makes
each probe expensive; network tokenisation makes stolen credentials less portable;
scheme-level and acquirer-level velocity controls see cross-merchant traffic that no
single merchant sees. A merchant-side detector is not where the leverage is.

Two things I would say next. First, **"solved by rules" is exactly the claim that
should be measured**, and my evaluation is built to measure it: my headroom metric
compares the detector against a naive decline-ratio rule, and the answer on the hardest
control is that the detector's headroom is **negative** — the naive rule does as well.
That is a finding in favour of the objection, and it is in my own failure-modes
document.

Second, the framing of this project is not "card testing is unsolved." It is: this is a
well-understood problem with a public shape, which makes it a **good test bed for
whether an evaluation can be trusted.** The contribution is the measurement discipline
— negative controls, base-rate honesty, retractions — and that transfers to problems
that are not solved.

**Q148. "How long before an adaptive attacker makes this useless?"**

Quickly, and I can be specific about how — which is more useful than a number.

**Three cheap evasions.** Spread the probes: `slow_low` is a mild version and already
my weakest scenario at 0.445 event recall; spacing attempts across hours defeats a
5-minute window entirely. Rotate the BIN: the primary axis is BIN velocity, and a
tester working across many issuers dilutes every per-BIN term. Walk the baseline: the
detector learns from the traffic it sees, so a slow ramp becomes the new normal —
`baseline_sample_interval_ms` limits how fast that can happen but does not prevent it.

**None of these is tested here.** My scenarios are fixed signatures generated from a
config, and `ASSUMPTIONS.md` §2.10 notes even the decline ratio within an episode is
constant. **UNANSWERED: no adaptive or adversarial attacker was ever modelled.**

The structural answer is that detection alone is the weakest layer of a card-testing
defence — authentication and tokenisation attack the economics, and a detector attacks
the symptom. Where a detector like this earns its place is raising the cost of the
*cheap* version of the attack, and being the thing that notices when someone stops
being cheap.

**Q149. "You used an AI agent to build this. What did you actually do?"**

I would answer this directly and without spin, because the project's entire claim is
about honest reporting and getting cagey here would undercut it.

**The code was written with an AI assistant. What I did was direct, review, and gate
it** — and in this project that was the load-bearing role, which I can evidence rather
than assert.

Every phase ended at a written gate, and the gate notes are in `docs/PHASES.md` as
eight review passes. The things that changed the project's outcome came from those
reviews: **the threshold criterion was wrong** — maximising net rupees on a cost model
that prices false positives at nearly zero — and that was caught in review and replaced
with a constrained one; **the frontier grid was too coarse**, hiding the whole 6–28
alerts/day band; **the parity fixture never saturated a ring buffer**, which nobody
would see by reading 0e0 — that was a review instruction to measure coverage; **the
batch=1 Python benchmark was too good** and got the "an unexpectedly good number
deserves the same suspicion" treatment, which found a real artifact; **the LLM
cassettes were read against the schema**, which is how the fabrication was found.

The judgment calls that shaped the submission were mine: freeze the detector rather
than chase the diagnosed fix; keep the retraction verbatim; lead with 0.0824 rather
than 0.4462; keep the flawed cassette rather than re-prompt.

What I would not claim is that I typed every line. What I would claim is that **I know
what every number means, where it came from, and which of them I do not trust** — and
this document, plus the fact that I can talk through any failure in it, is the
evidence.

**Q150. "Your detector isn't deployable. Why are you presenting it?"**

Because "not deployable" is a **measured conclusion**, and arriving at it honestly is
the result.

Most projects at this level cannot tell you whether their detector is deployable,
because they never built the thing that would answer the question. I can tell you
precisely: it declines **1 in 71 legitimate customers**, and the reason is a specific
named blind spot — legitimate traffic whose declines concentrate on one BIN at one
merchant — with the mechanism worked out to the level of "1.13 in-window attempts per
card against 1.22, so the damping term points the wrong way."

That is a more useful artifact than a detector with a good-looking number and no idea
where it fails.

What is deployable-quality here is the **apparatus**: the temporal split with a guard
that refuses anything else, the base-rate reweighting, the rupee model with break-even
as an output, the negative controls, the multi-seed default, the bit-exact two-engine
port, and a boundary proving no language model touched a number. Point those at a
different detector — or a learned one — and they work unchanged.

And the honest closing line: **it detects reliably and fast, and it is not deployable
as an inline control.** Both halves are measured, and I would rather be the candidate
who says the second half out loud than the one who has never checked.

---

# 13. THE TEN HARDEST QUESTIONS, RANKED BY EXPOSURE

Assessed as: how badly does this land if a sharp panellist asks it?

**1. "Show me your baseline comparison." — SEVERELY EXPOSED.**
There is none. No logistic regression, no gradient boosting, nothing. The core design
claim is untested, the experiment is cheap, the data and metrics already exist, and
there is no good reason it is absent beyond prioritisation. *Only mitigation: concede
immediately, name it as the weakest part of the project (Q135), and have the two-week
plan ready with it first.*

**2. "What does it do on real traffic?" — SEVERELY EXPOSED, and unfixable here.**
Entirely unknown. Every number is a synthetic-stream number, and `ASSUMPTIONS.md` §2
names three distortions that flatter the results. *Mitigation: the engineering/numbers
split (Q145) — believe the apparatus, treat the performance as unvalidated. Do not
defend external validity; bound it.*

**3. "You never ran the random-split comparison you argue against." — EXPOSED.**
The loader refuses non-temporal splits, so the guard exists but the demonstration does
not. It would have been one afternoon and a persuasive slide. Answering "I built the
guard instead of the comparison" is honest but thin.

**4. "How sensitive is any of this to your six weights and your half-life?" —
EXPOSED.**
No sensitivity analysis on `w_*` or `ewma_halflife_samples = 30` exists. The threshold
got a 600-point sweep and a nine-point frontier; the weights got nothing. A panellist
who notices the asymmetry has a fair point about where the rigour was spent.

**5. "Your cost model's biggest term rests on two uncitable assumptions." —
MODERATELY EXPOSED, but pre-conceded.**
True: the net is dominated by chargeback exposure, the product of a ₹500 fee and an
0.8 rate, and halving the rate roughly halves the saving. *Mitigation: `costs.toml`
labels them ASSUMPTION in the file, §6 states the sensitivity, and the break-even
inversion exists precisely so the headline does not rest on the weakest input.*

**6. "Both ablations beat your detector and you shipped it anyway." — MODERATELY
EXPOSED.**
Defensible but requires the full argument every time: net rupees varies 1.3× across
the frontier while realistic-base-rate precision varies 2.7×, so the metric they win
on is the blind one. The risk is that it sounds like motivated reasoning — which is why
it matters that §0.1 established the blindness **before** the ablation table existed.

**7. "Why is there no CI?" — MODERATELY EXPOSED, with a good story attached.**
None exists, and the fresh-clone run proved exactly what it would have caught. The
saving grace is that the failure was found, diagnosed, fixed and logged rather than
shipped — but "I approximated CI once, manually, at the end" is a weaker answer than
having it.

**8. "Your worst failure has an unbuilt fix. Convince me the freeze was right." —
MILDLY EXPOSED.**
Strong answer available (Q137): changing the reference moves the parity spec, and the
freeze is what makes the bit-exact result meaningful. The exposure is that a panellist
may simply value the fix over the parity result — a legitimate difference in judgment
that no evidence settles.

**9. "Multi-threaded throughput? Other platforms? p99.9?" — MILDLY EXPOSED.**
All unmeasured. One Windows machine, single-threaded, p99 as the deepest percentile.
The GIL is released so concurrency is *possible*, but "possible" is not "measured", and
for an inline control the deep tail is a fair thing to ask about.

**10. "How does it hold up against an adaptive attacker?" — MILDLY EXPOSED.**
Unmodelled and unmeasured. Mitigated by being able to name three specific cheap
evasions and the structural reason detection is the weakest defensive layer (Q148) — a
candidate who can enumerate their own evasions is in better shape than one who claims
robustness.

**A note on the shape of this list.** The two severe items are both about what was not
built rather than what was built wrong, and both were conscious trade-offs against an
eleven-day schedule. That is the honest summary of the project's exposure: the
apparatus is stronger than the detector, and the biggest risks are the experiments that
were never run.

**And one that is not on the list because it is fixable before the panel.** The stale
figures in `FAILURE_MODES.md` §2.1/§6 (see the note at the top of this file) would be
an eleventh exposure if left standing — a reviewer opening two documents and finding
50.5% in one and 39.9% in the other has found an inconsistency in the project's most
important document, in the section where the panel forms its opinion. Refresh the
source documents and it disappears. Leave it, and it costs more credibility than the
number itself does, because this project's whole claim is that its figures can be
trusted to match its build.
