# Pitch-video checklist

Candidates flagged at the phase gates, in rough slide order. The through-line
of the pitch is the mechanism sentence, said once, verbatim: **"none of these
were caught by staring harder at the output — each was caught by a guard, a
re-run under different conditions, or someone asking whether the number should
be true."**

## Open (30s)
- [ ] What Spandan is, one sentence: defense-only streaming card-testing
      detector, one spec, two bit-exact engines, rupee-honest evaluation.
- [ ] The headline said unflinchingly: precision 0.0824 at a realistic base
      rate — eleven false alarms per catch — and 1 in 71 legitimate customers
      declined. "It detects reliably and fast, and it is not deployable as an
      inline control." Leading with this buys credibility for everything after.

## What it does well (60s)
- [ ] Detection speed is the best slide (Phase 2 gate): 60/60 episodes, p90 of
      32/6/7 events to first flag — and say why the median-0 version of this
      slide would be dishonest (saturated medians are what over-triggering
      looks like).
- [ ] The engine-swap headline (Phase 3→4): `make eval ENGINE=rust` vs
      `python` over 1.6M events — the metrics JSONs differ in one line, the
      engine label. Show the diff on screen; it is three lines.

## Why Rust, in one slide (45s)
- [ ] `Axis` has no `Card` variant: the card-novelty ban as compile error >
      test failure > documented promise. The type system as the strongest of
      three enforcement strengths.
- [ ] The trade, win and cost in the same sentence, verbatim from README:
      5.5× streaming, 4.8× better p99, at 2× memory per entity, 31 GB vs
      16 GB/month projected. Never the throughput without the memory.
- [ ] "Closing a 2× constant on an unbounded curve is polish, not the fix."

## The honest-measurement spine (90s — the differentiator)
- [ ] The plausible-number pattern as ONE story, five instances: single-seed
      precision 1.00; the never-saturating "bit-exact" fixture; 0.0 MB RSS
      from an unchecked Win32 call; the batch=1 bench that re-scored one hot
      event; the patch script's unchecked empty grep. Then the mechanism
      sentence, verbatim.
- [ ] The two retractions as the same habit applied to results: the EWMA
      ablation (withdrawn on our own initiative, then re-measured), the
      coarse-grid "improvement" (superseded, not swapped).
- [ ] The flash-sale novelty leak (Phase 1): a property test caught the
      generator making the benign control separable by entity novelty alone —
      the bug that would have made every later number a lie.
- [ ] "What does a flag do": the dedup finding — 20,254 flagged events inside
      a 10-alerts/day budget — and how resolving the semantics (flags decline)
      replaced a flattering number (alerts/day) with the real one (1 in 71).
- [ ] Threshold discipline: budget registered in costs.toml before the test
      window was read (commit e5b48f8); the frontier as a sensitivity
      analysis, not a menu.

## Parity, framed correctly (30s)
- [ ] The escape hatch going unused is evidence the paperwork was the work:
      (t−W, t] and the six-term summation order written down before any Rust
      existed. Parity risk is spec ambiguity, not arithmetic.
- [ ] And the fixture was still under-covering until review forced a
      saturating burst — "bit-exact on the happy path" as a trap named live.

## The LLM boundary (45s)
- [ ] The poisoned-import test on screen: the full evaluation runs
      bit-identically while `spandan.llm` raises on any attribute access.
      "Not 'we didn't' — 'we structurally could not have'."
- [ ] The negative finding, told straight: the recorded model fabricated
      CVV/AVS grounds for a block; the comparison target was committed before
      any LLM code existed; the template ships because substitution cannot
      fabricate. The flawed cassette stays — re-prompting for a nicer sample
      is threshold-selection on the test set, again.

## Close (15s)
- [ ] What was recorded rather than built (FAILURE_MODES §7), and that the
      deployment-blocking failure (§2.1 issuer outage) is named, measured,
      and diagnosed — the project knows where it ends.
