//! spandan-core — Phase 0 skeleton.
//!
//! There is no detector logic here yet. The five modules named in PHASES.md
//! Phase 3 (`ingest`, `state`, `velocity`, `baseline`, `score`) are out of scope
//! until that phase is handed over.
//!
//! `_smoke_add` exists for exactly one reason: to prove that the
//! PyO3 / maturin / abi3-py310 toolchain builds and imports on this machine
//! before anything depends on it. **It is deleted in Phase 3**, which asserts its
//! absence with a grep.

use pyo3::prelude::*;

/// Toolchain smoke test. Deleted in Phase 3 — see PHASES.md.
#[pyfunction]
#[allow(non_snake_case)]
fn _smoke_add(a: i64, b: i64) -> i64 {
    a + b
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(_smoke_add, m)?)?;
    Ok(())
}
