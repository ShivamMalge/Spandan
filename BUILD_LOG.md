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
