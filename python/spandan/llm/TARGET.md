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

## Verdict

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
