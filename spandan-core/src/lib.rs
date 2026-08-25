//! spandan-core — the deterministic streaming detector core.
//!
//! Five modules, in the order an event flows through them:
//!
//! | stage | module | responsibility |
//! |---|---|---|
//! | 1 | [`ingest`]   | untyped input becomes a typed [`ingest::Event`] |
//! | 2 | [`state`]    | per-entity state, keyed by (axis, id) |
//! | 3 | [`velocity`] | fixed-capacity sliding windows and their aggregates |
//! | 4 | [`baseline`] | Welford and EWMA baselines, fed on a sample gate |
//! | 5 | [`score`]    | evidence terms minus damping terms |
//!
//! There is no sixth module, by decision rather than by omission.
//!
//! ## Parity
//!
//! This core is a port of `python/spandan/detect/reference.py`, which is frozen
//! for the duration of Phase 3 and is the specification. `tests/parity.rs`
//! replays a committed fixture of that reference's scores and requires agreement
//! to a stated tolerance. The Python side is the authority; where this core and
//! the reference disagree, this core is wrong.
//!
//! ## Defense-only
//!
//! This is a detector. It consumes transaction records and emits scores and
//! evidence. It contains nothing that generates, mutates, or replays payment
//! traffic.

pub mod baseline;
pub mod ingest;
mod pybridge;
pub mod score;
pub mod state;
pub mod velocity;

pub use ingest::{Event, Status};
pub use score::{Contributions, Detector, DetectorConfig, Flag};
pub use state::Axis;

use pyo3::prelude::*;

/// The Python-facing module. `pybridge` is a translation layer only - every
/// scoring decision lives in the five core modules under the parity fixture.
#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_class::<pybridge::PyDetector>()?;
    Ok(())
}
