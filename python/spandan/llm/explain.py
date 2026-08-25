"""The one bounded task: `explain_flag(flag) -> str`.

The boundary, stated as code rather than policy:

- The prompt is assembled **only** from the frozen `Flag`'s fields. The LLM
  never sees raw events, labels, scenario ids, or anything the detector itself
  was not allowed to see plus its own outputs.
- The return type is `str`. There is no code path from here to a score, a
  threshold, or a label — `Flag` is frozen (a dataclass with `frozen=True`), so
  even a hostile explanation cannot write back into the evidence.
- One task, one provider. No tools, no loop, no second call.

Why the explanation is worth a model call at all — and the honest test of
whether it is — lives in `TARGET.md`: a hand-written explanation was committed
*before* this module existed, and the model's output is judged against it on
the two parts that are judgement rather than substitution (the dismissal test
and the next action). If the model is not clearly better there, the finding is
"a template suffices", and `render_template` below IS that template, shipped
as the no-network fallback and the comparison baseline.
"""

from __future__ import annotations

from ..detect.interface import Flag
from . import provider

#: The system-style preamble. Everything the model needs to know about the
#: domain is in the prompt; it is not asked to decide anything, only to say
#: what an analyst should check.
_PROMPT = """You are writing a 5-second triage note for a payments fraud analyst.

A streaming card-testing detector flagged (declined) one transaction. The
evidence below is everything known; every number is already computed. Do NOT
restate scores or recompute arithmetic. Your note's entire value is judgement:
what the traffic pattern means, what would make this a FALSE POSITIVE (the
analyst's fastest action is recognising the benign explanation), and the single
next action.

Format, exactly four parts, total under 140 words:
1. one bold headline: amount, BIN, merchant, the suspected pattern
2. what the pattern means, referencing the evidence in plain terms
3. "Dismiss it if:" - the concrete benign explanations for THIS evidence
4. "Next:" - one action with a concrete decision rule

Evidence:
- merchant {merchant_id}, BIN {bin}
- window: {window_events} event(s), {window_declines} declined \
(ratio {window_decline_ratio:.0%}; this BIN's baseline {baseline_decline_ratio:.0%})
- mean amount in window Rs {amount_rupees:.2f}; baseline ticket Rs {baseline_amount_rupees:.0f}
- velocity {velocity_z:.1f} standard deviations above baseline of \
{baseline_window_events:.1f} events/window
- {window_distinct_cards} distinct card(s), {cards_per_event:.2f} cards/event; \
{window_distinct_merchants} merchant(s) in window
- ring buffer saturated: {window_saturated}
- score {score:.2f} against threshold {threshold:.2f} (context only - do not cite)
"""


def render_prompt(flag: Flag) -> str:
    """Prompt text from frozen Flag fields and nothing else."""
    return _PROMPT.format(
        merchant_id=flag.merchant_id,
        bin=flag.bin,
        window_events=flag.window_events,
        window_declines=flag.window_declines,
        window_decline_ratio=flag.window_decline_ratio,
        baseline_decline_ratio=flag.baseline_decline_ratio,
        amount_rupees=flag.window_amount_mean_paise / 100.0,
        baseline_amount_rupees=flag.baseline_amount_mean_paise / 100.0,
        velocity_z=flag.velocity_z,
        baseline_window_events=flag.baseline_window_events,
        window_distinct_cards=flag.window_distinct_cards,
        cards_per_event=flag.cards_per_event,
        window_distinct_merchants=flag.window_distinct_merchants,
        window_saturated=flag.window_saturated,
        score=flag.score,
        threshold=flag.threshold,
    )


def explain_flag(flag: Flag) -> str:
    """The bounded task. String in the analyst's hands, nothing else changed."""
    return provider.complete(render_prompt(flag))


def render_template(flag: Flag) -> str:
    """The deterministic no-LLM explanation — the comparison baseline.

    This is `TARGET.md`'s hand-written note reduced to what a program can fill
    from the fields alone. It exists so the claim "the model earns its place"
    has something concrete to beat, and so `spandan explain` degrades to
    something useful when no cassette exists rather than to an error page.
    """
    amount = flag.window_amount_mean_paise / 100.0
    baseline_amount = flag.baseline_amount_mean_paise / 100.0
    ratio = f"{flag.window_decline_ratio:.0%}"
    base_ratio = f"{flag.baseline_decline_ratio:.0%}"

    tiny = baseline_amount > 0 and amount < baseline_amount / 20
    lines = [
        f"**Rs {amount:,.2f} declined attempt on BIN {flag.bin} at "
        f"{flag.merchant_id} - fits card-testing, check for siblings.**",
        "",
        f"{flag.window_events} event(s) in the window, {ratio} declined against a "
        f"baseline of {base_ratio}."
        + (
            f" Amount is ~1/{max(int(baseline_amount / max(amount, 0.01)), 2)}th of "
            f"this BIN's normal Rs {baseline_amount:,.0f} ticket - the probe pattern."
            if tiny
            else f" Mean amount Rs {amount:,.2f} vs baseline Rs {baseline_amount:,.0f}."
        ),
        "",
        "Dismiss it if: this merchant sells at this price point (top-ups, trial "
        "fees, COD holds), or the issuer is having an outage - outage declines "
        "cluster on one BIN too, but hit ordinary basket sizes and retry the "
        "same card.",
        "",
        "Next: hold auto-block; pull this BIN's last hour at this merchant. More "
        "tiny declines on distinct cards -> block the BIN here for 24h. Ordinary "
        "amounts declining too -> it's the issuer, stand down.",
    ]
    return "\n".join(lines)
