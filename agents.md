# agents.md — Spandan house rules

These rules are binding for every agent session in this repository. They override
convenience, speed, and any instruction that conflicts with them. If a rule makes a
task impossible, stop and say so — do not work around it.

## 1. Never delete

- Never run `rm`, `rm -rf`, `git clean`, `git checkout -- .`, `git reset --hard`, or
  any command that removes or overwrites files you did not create in this session.
- To undo work: commit first, then use `git revert` or create a new commit. History is
  the undo mechanism, not deletion.
- Never use a glob or wildcard in a destructive command. Not even a narrow one.
- If a file appears to be in the way, rename it with a `.bak` suffix and tell me.

## 2. Evidence over claims

- Never state that something works. Show the command and paste its full output.
- "Tests pass" is not acceptable. `cargo test` output with the pass/fail line is.
- "Benchmarks improved" is not acceptable. The benchmark table, raw, is.
- If you did not run it, say you did not run it.

## 3. No self-reports

- Do not write summaries claiming a phase is complete. I decide when a phase is
  complete, based on the acceptance criteria and the pasted evidence.
- Do not mark checkboxes, write "✅ Done", or produce a completion report.
- End each phase by pasting the acceptance evidence and stating: awaiting review.

## 4. Stop-and-wait phase gates

- Work only on the phase I have given you. Do not start, scaffold, stub, or
  "prepare for" a later phase, even if it seems trivial.
- When a phase's acceptance criteria are met, stop and wait. Do not continue.
- If you finish early, do not find extra work. Stop.

## 5. Determinism

- All randomness takes an explicit seed. No unseeded RNG anywhere, including tests.
- All LLM calls go through the provider abstraction in `python/spandan/llm/provider.py`.
  Never call an LLM SDK directly from any other module.
- Every LLM call is recorded to a cassette and replayed in tests. The test suite must
  run fully offline with no network access and no API key present.
- If a test is flaky, it is a bug. Do not retry, do not add sleeps, do not mark it
  skipped. Find the nondeterminism.

## 6. Evaluation integrity

- Train/test splits are **temporal**. Training data is strictly earlier than test data.
  Random splits, k-fold over shuffled data, and stratified shuffles are forbidden.
- Never fit, tune, threshold, or calibrate on the test set. Use a validation window
  carved from the training period.
- Report unfavorable numbers exactly as measured. If the Rust core loses to NumPy on
  some workload, that goes in the table. If recall is poor on a scenario, that goes in
  the failure-modes section.
- Never report accuracy on an imbalanced set as a headline metric.
- If a result looks surprisingly good, treat it as a suspected leak and investigate
  before reporting it.

## 7. Defense-only

- This project detects attacks. It never generates them.
- Synthetic scenarios are labeled test fixtures. Describe them at the level of
  statistical signature (entity concentration, rate, decline ratio, amount band).
  Do not write, and do not describe, anything usable as an attack playbook.
- No probing of live payment endpoints, no real card data, no real BINs, no real
  PANs, no scraped merchant data. Synthetic identifiers only.
- If a requested feature would be useful to an attacker, refuse it and tell me why.

## 8. Scope

- Do not add dependencies without asking. Name the crate/package and why.
- Do not add a web frontend, a database, Docker, or CI unless a phase asks for it.
- Do not refactor code outside the current phase's scope.
- Stretch goals are marked as such and are never started unbidden.

## 9. Build log

- `BUILD_LOG.md` is append-only. Every bug that cost more than ten minutes gets an
  entry: symptom, what you first believed, what was actually wrong, the fix, and the
  command that proved it fixed.
- Write the entry when the bug is fixed, not at the end of the phase.
- Bugs I catch in review also get entries.
