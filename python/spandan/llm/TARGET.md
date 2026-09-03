# The hand-written target explanation

Written **before** any LLM code exists in this repository, per the Phase 5 gate
instruction, and committed as the bar the model output must clear. The
comparison verdict lives at the bottom of this file once the model side exists.

## The bar

An explanation earns its place only if an analyst can **act on it or dismiss it
in five seconds**. That means it must carry, in order of usefulness:

1. which BIN and merchant, and what the traffic looked like (not the score),
2. the rupee exposure if this is real,
3. **what would make this a false positive** — the dismissal test, because an
   analyst's fastest action is recognising the benign explanation,
4. the one next action.

It must NOT restate the score ("flagged because velocity exceeded baseline by
12σ" is the template with extra steps). The arithmetic is already on the flag;
the explanation's only job is the part the arithmetic cannot say.

## Target, written by hand from flag `txn_000804993`

Evidence available to the writer (frozen `Flag` fields, nothing else):
merchant `mer_008`, BIN `099813`, score 24.28 vs threshold 21.99, window of 1
event: declined, ₹5.45 against this BIN's baseline ticket of ₹2,833 and baseline
decline rate 9.9%; no velocity elevation; single merchant; single card, no
retry.

> **₹5 declined card attempt on BIN 099813 at mer_008 — fits card-testing, check
> for siblings.**
>
> A single ₹5.45 attempt, declined, on a BIN whose customers normally spend
> ~₹2,833 a ticket and get declined about 1 time in 10. A 100%-declined window
> at 1/500th of the normal ticket is the probe pattern: testers try tiny
> amounts to learn whether a stolen card is live, and a real customer has
> little reason to charge ₹5 here.
>
> **If it's real:** this card is one of a batch. The loss isn't this ₹5 — it's
> the chargebacks on whichever cards come back "approved". Expect siblings on
> this BIN within the hour.
>
> **Dismiss it if:** this merchant sells anything at this price point (top-ups,
> trial fees, COD verification holds), or issuer `0998xx` is having an outage —
> outage declines cluster on one BIN too, but they hit *ordinary* basket sizes
> and re-try the *same* card. This window shows neither, which is why it
> flagged.
>
> **Next:** hold auto-block; pull this BIN's last hour at this merchant. More
> tiny declines on distinct cards → block the BIN here for 24h. Ordinary
> amounts declining too → it's the issuer, stand down.

Twelve seconds to read in full; the bold line and the dismiss line alone are
five. Every number above is copied from the flag; nothing is computed fresh.

## What this implies for the model

The model receives the same frozen fields and must beat this on the only axis
that matters: the quality of the *dismissal test* and the *next action* — the
two parts that are judgement rather than substitution. If its output is not
clearly better there, the honest finding is "a template suffices" and this file
is the template.

## Verdict, against the recorded model output

Recorded 2026-08-26: `gemini-3.1-flash-lite` over the wire, both committed
flags, cassettes `9738bd8f…` and `7e36f73e…` kept exactly as they came back —
not re-prompted, not curated. Re-prompting until a nicer sample appears and
shipping that one would be the same error as selecting a threshold on the test
set: the flawed output IS the finding.

Recorded 2026-09-03: `claude-haiku-4-5` through the Anthropic API, the ₹5.45
probe with the plain prompt (`a39301b4…`, accepted by the validator) and with
the grounded prompt (`424749a7…`, rejected), both kept exactly as returned. The
accepted note is grounded — nothing outside the prompt — and not better than
this target: its dismissal test is generic (processing error, gateway timeout,
small legitimate purchase), its next action has no decision rule, and it
contradicts itself on whether a tiny amount points toward or away from card
testing. The grounded-prompt note reaches for the decline code and AVS result
it was told do not exist, and calls the BIN's baseline "this card's". Against
this target, on this case: the template is at least as good, and it is
deterministic.

The ₹150 case with the plain prompt (`3cf90c49…`, rejected: names the merchant
category and builds its decision rule on a 50% threshold that is in no
evidence) is the one that matters for the claim below. Over the wire the model
did **not** re-rank toward the sale: it read ₹150 as an "atypical low amount"
and dismissed the flag as n=1 noise, with no mention of a price point. The
paragraph below describes the in-context note, written by an author who had
read this target; it did not reproduce on the first wire sample, and it stands
as a description of that note, not of what the API returns. The grounded-prompt
recording of this case (`767d03f7…`, rejected on naming CVV/AVS while marking
them unavailable) did not re-rank toward the sale either. Two wire samples,
zero re-rankings: on the ambiguous case the model call has not earned its place
over the template.

**The Gemini output is not better than the hand-written target. It is worse, in
two ways that matter more than style.**

**1. It fabricates evidence.** The ₹5.45 note's only decision rule is "Block
the BIN for 24 hours if the CVV/AVS result on this attempt returned
'Mismatched' or 'Not Supported'" — there is no CVV or AVS field in the `Flag`,
the prompt, or anywhere in this pipeline. The ₹150 note orders "If no
successful prior history exists at this merchant, blacklist the card and
cardholder IP immediately" — per-card history and cardholder IP are equally
absent from the evidence it was given. Both "Next:" actions are therefore
conditioned on data the analyst does not have: confident grounds for a block
that nothing in the system can supply. Note that the prompt already said "the
evidence below is everything known" — the fabrication happened anyway, which
is itself a finding: prompt discipline is not a boundary.

**2. It misrepresents what the detector measured.** Both notes tell a
single-credential causal story — "a bot testing a single stolen credential",
"confirms this is a programmatic validation" — when the detector fires on
velocity and decline-ratio deviation across an entity's window against learned
baselines. The ₹150 note also attributes the baseline ticket to "the
merchant's average" when it is the BIN's baseline. An explanation that
misstates the detection basis teaches the analyst a wrong mental model of the
alarm, which compounds rather than helps.

And on the one axis the superseded comparison below credited a model with —
re-ranking hypotheses on the ambiguous case — the recorded output does not do
it. The ₹150 note never considers that ₹150 is a plausible real price point;
"check for a sale first" is entirely absent, on the event that *is* a
flash-sale false positive.

**Finding: a template suffices. `render_template` stands as the shipped
explanation.** The template cannot fabricate, because substitution can only
place fields that exist; on this evidence that property is worth more than
fluency. The model call remains in the codebase as what it now honestly is:
a measured negative result with the blast radius bounded by architecture —
see FAILURE_MODES §8 for why a hallucinating explainer here degrades prose
but cannot corrupt a number.

**Addendum, 2026-09-03: the boundary now applies to the prose too.**
`llm/grounding.py` rejects any note that cites evidence outside the prompt it
was generated from; `explain_flag` validates before returning, so an analyst
never receives a note conditioned on data the system does not have. Both
recorded notes above are rejected by it — the ₹5.45 note on CVV/AVS, the ₹150
note on reason code, per-card history and IP — and the template passes by
construction. That does not change this verdict: the model output was worse
than the target, and now it is also caught. What it changes is the disposition
from "template, because the model cannot be trusted" to "model note when it is
grounded, template when it is not" — with the count so far at 0 of 2 grounded.

## Superseded: the pre-recording comparison, against in-context notes

> **Superseded 2026-08-26 by the verdict above.** The notes this section
> compares were authored in-context before any API key existed; their
> cassettes were removed when the wire recordings replaced them and live in
> git history (commit `8a6bf8d`), `recorded_via` truthful. The wire recording
> that replaced them reversed the conclusion — and this section's own caveat
> said its result was "evidence of *capability*, not of what an arbitrary API
> call returns; re-record over the wire before quoting it as the latter." The
> re-record was made, and the arbitrary API call fabricated evidence. Kept
> because a reversed conclusion that gets deleted gets re-learned.

Three artifacts now exist: this hand-written target, the deterministic
`render_template` (the target reduced to what `str.format` can fill), and the
cassette notes. Compared on the two axes that matter:

**On the clean case (`txn_000804993`, the ₹5.45 probe), a template suffices.**
The model note adds one genuine observation the template misses — the card was
not retried, which fits a probe and not a failed customer — and a more concrete
decision rule ("three or more sub-₹50 declines on distinct cards"). Marginal.
If the project shipped only this case, the honest conclusion would be that the
LLM does not earn its place, and the template would stand.

**On the ambiguous case (`txn_000806675`, ₹150), the free-form note does what
the template structurally cannot: it re-ranks the hypotheses.** The template's
headline calls everything "fits card-testing"; the note reads ₹150 as a real
price point rather than a probe amount, leads with "check for a sale first",
and reorders the next action around that check — for an event that *is* a
flash-sale false positive, which the model was never told. Judgement about
which benign explanation is most likely, given this particular evidence, is
the one thing a fill-in template cannot vary. That is the narrow claim for the
model call, and it is conditional on the case being ambiguous.

**Caveats that bound this verdict, stated rather than buried.** The cassette
notes were authored in-context by the same Anthropic model that built this
project (no API key existed in the build environment — the cassettes' own
`recorded_via` says so), by an author who had read this target. The comparison
is therefore biased toward the model side and is evidence of *capability*, not
of what an arbitrary API call returns; re-record over the wire before quoting
it as the latter. What this phase actually earns its place with is the
boundary: the poisoned-import test proving no evaluation number can pass
through a language model is worth more than any prose above.
