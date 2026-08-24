# BUILD_LOG.md

Append-only (`agents.md` §9). Every bug that cost more than ten minutes gets an
entry, written when the bug is fixed rather than at the end of the phase. Bugs
caught in review get entries too.

Entry format:

```
## <date> — <one-line symptom>
**Phase:** <n>
**Symptom:** what was observed.
**First believed:** the wrong diagnosis, stated plainly.
**Actually wrong:** the real cause.
**Fix:** what changed.
**Proved by:** the command whose output showed it fixed.
```

## 2026-08-24 — `pip install -e .` failed: maturin could not find `python/spandan_core`
**Phase:** 0
**Symptom:** `python -m pip install -e '.[dev]'` died in metadata generation:
`python-source is set to .../python, but the python module at .../python/spandan_core
does not exist.` The Rust crate itself compiled fine; the failure was in maturin's
PEP 517 hook, before any linking.
**First believed:** a `module-name` typo, or that `abi3-py310` needed a Python 3.10
interpreter present (3.11.9 is what is installed).
**Actually wrong:** neither. In maturin's mixed rust/python layout, the compiled
extension must live *inside* a package directory under `python-source` — a top-level
`module-name = "spandan_core"` with no `python/spandan_core/` directory is not a
valid arrangement. The abi3 tag and the interpreter version were never involved.
**Fix:** built the extension as the private submodule `spandan_core._native`
(`module-name = "spandan_core._native"`, `#[pymodule] fn _native`) and added
`python/spandan_core/__init__.py` re-exporting its surface, so callers still write
`import spandan_core` as PHASES.md Phase 0 and Phase 4 specify.
**Proved by:** `maturin develop --release` → `🛠 Installed spandan-0.1.0`, then
`python -c "import spandan_core as s; print(s.__version__, s._smoke_add(2,3))"` →
`0.1.0 5`.
**Note for Phase 4:** this is the first of the two PyO3 packaging gotchas PHASES.md
expects; the zero-copy NumPy one is still ahead.

## 2026-08-24 — the benign flash sale was separable by entity novelty alone
**Phase:** 1
**Symptom:** `test_flash_sale_is_a_mixture_of_known_and_new_customers` (then named
`test_flash_sale_reuses_benign_entities`) failed: only **66%** of flash-sale cards
had ever appeared in benign traffic, against an expected >90%.
**First believed:** an off-by-something in the test — the flash-sale generator does
draw its cards from the benign pool, so overlap "should" have been total.
**Actually wrong:** the generator was correct about the *pool* and wrong about the
*distribution*. Benign traffic draws cards Zipf(1.35)-weighted, so most of the
9,000-card pool never transacts; the flash sale drew **uniformly** over the same
pool, so roughly a third of its customers were identifiers that appear nowhere else
in the stream. The negative case was therefore partly separable by novelty alone —
"many unseen cards" would have distinguished sales from attacks for free, and the
only false-positive test in the project would have been measuring the wrong thing.
This would not have shown up as a failure anywhere downstream; it would have shown
up as suspiciously good precision in Phase 2.
**Fix:** flash-sale entities are now an explicit mixture — 75% known customers drawn
with the *same* popularity weights as benign traffic, 25% genuinely new customers
with unseen cards, IPs and devices (`flash_sale_new_entity_fraction`). Both halves
are deliberate: all-known would make novelty a free separator one way, all-new the
other way. Recorded in `gen/ASSUMPTIONS.md` §1.7 as the most load-bearing choice in
the generator.
**Fix commit:** the test now asserts the mixture from both sides (`0.55 < known <
0.95`) and additionally asserts that attack cards share nothing with benign cards.
**Proved by:** `python -m pytest tests/test_gen.py -v` → 15 passed, including
`test_flash_sale_is_a_mixture_of_known_and_new_customers`.
**Worth noting:** the test that caught this was written to measure a property rather
than to check that the code did what it was written to do. A test asserting "flash
sale cards come from the benign pool" would have passed.
