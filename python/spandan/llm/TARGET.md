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

*(left open until the model side exists — see the comparison at the foot of
this file once cassettes are recorded)*
