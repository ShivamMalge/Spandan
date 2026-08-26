# Architecture

The five-minute walk. Every box below is a directory or module you can open;
every claim on an edge is enforced by a test named in the text that follows.

```
            synthetic stream generator          python/spandan/gen/
            ---------------------------
            reserved-range identifiers ONLY: ISO/IEC 7812 MII-0 BINs,
            RFC 5737 / RFC 2544 IPs, opaque non-PAN card tokens.
            Attacks are statistical signatures, never procedures.
            Byte-identical builds (gzip mtime=0), manifest per stream.
                              |
                              v
              events stream, temporally split
              train  |  validation (train suffix)  |  test
                              |
                              v
     +----------------- detector: one spec, two engines -----------------+
     |                                                                   |
     |   python reference                    rust core                   |
     |   detect/reference.py                 spandan-core/ via PyO3      |
     |   THE SPECIFICATION (frozen)          abi3 wheel, zero-copy       |
     |   rich Flag evidence per event        numeric columns, f64 out    |
     |          \                                 /                      |
     |           bit-exact parity, both directions:                      |
     |           - committed 3,866-event fixture incl. a ring-saturating |
     |             mega-burst; tolerance 1e-9, achieved 0e0              |
     |           - full 1.6M-event eval: byte-identical metrics JSON     |
     |             (the only differing line is the engine label)         |
     +-------------------------------------------------------------------+
                              |
                              v
            per-entity sliding windows over (t-W, t]
            axes: BIN / IP / DEVICE / MERCHANT
            -- there is NO Card axis: the card-novelty ban is a
               compile error in Rust, a test failure in Python,
               and a promise in ASSUMPTIONS.md 1.7a
            ring buffers capped at 512/entity (saturation recorded);
            Welford + EWMA baselines, folded only AFTER scoring
                              |
                              v
            six score terms, fixed summation order
            (velocity_bin, decline_bin x10, amount, velocity_ip,
             -repetition, -merchant_span)
                              |
                              v
        flag = score > threshold  ==>  DECLINES the transaction
        alerts = flags deduped per (merchant, BIN), 15-min cooldown
                              |
          +-------------------+----------------------+
          |                                          |
          v                                          v
   evaluation harness                        explanation layer
   eval/harness.py                           llm/  -- OUTSIDE the
   ----------------                          number path, provably:
   threshold chosen on VALIDATION            - import-graph test: detect/
   only, under a constraint                    eval cannot import llm
   registered BEFORE the test                - poisoned-import test: full
   window was read (commit                     eval runs bit-identically
   e5b48f8): max net rupees                    with spandan.llm raising
   s.t. alerts/day <= 10                       on any attribute access
   three precisions, rupee cost              - replay-only by default;
   model (break-even as output),               sockets blocked in tests;
   multi-seed matrix, frontier                 cassettes committed;
                                               deterministic template
                                               fallback (the shipped
                                               explainer -- see
                                               FAILURE_MODES section 8)
```

## The walk, in prose

1. **Generation** (`python/spandan/gen/`). A configurable synthetic Indian
   payment stream: Poisson arrivals with diurnal shape, Zipf entity
   popularity, three attack scenarios and three negative controls injected as
   episodes. All identifiers come from reserved ranges; no real PAN, IP, or
   merchant exists anywhere in the repository. Builds are byte-identical for
   a given seed and recorded in a manifest.

2. **Detection** (`python/spandan/detect/`, `spandan-core/`). The Python
   reference is the specification — the window convention `(t−W, t]`, the
   baseline update order, and the six-term summation order were written down
   before any Rust existed, and the Rust core matches it bit-exactly: first
   on a committed fixture that saturates the ring buffers, then byte-identically
   across the full 1.6M-event evaluation. `make eval ENGINE=rust|python`
   selects the engine at one resolution point (`detect/rust_engine.py`).

3. **Semantics**. A flag **declines the transaction it is raised on** — an
   inline authorization control, stated in `detect/interface.py`, which is why
   the decline rate on legitimate traffic (1 in 71) is the deployability
   number rather than the alert count.

4. **Evaluation** (`python/spandan/eval/`). Temporal splits enforced by a
   loader that refuses anything else; threshold selection on validation only,
   maximizing net rupees subject to the alerts/day budget registered in
   `costs.toml` before the test window was read; alert dedup; three
   precisions; a rupee cost model whose review cost is an output (break-even
   ₹613/alert), not an input; a three-seed matrix and a budget frontier as a
   sensitivity analysis, not a menu.

5. **Explanation** (`python/spandan/llm/`). One bounded task — frozen `Flag`
   fields in, string out — kept outside the evaluation import graph, with
   tests that prove no evaluation number can pass through a language model.
   The recorded model output fabricated evidence (FAILURE_MODES §8), so the
   shipped explainer is the deterministic template; the boundary is the part
   that earned its keep.
