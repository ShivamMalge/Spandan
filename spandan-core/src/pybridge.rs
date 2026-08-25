//! The PyO3 surface. Phase 4's module — not one of the five core stages.
//!
//! Design rule: this file is a *translation layer only*. Every scoring decision
//! lives in the five core modules and is covered by the parity fixture; nothing
//! here may compute anything a test could not attribute to the core. If this
//! file did arithmetic, the engine swap would be comparing "Rust plus a second
//! implementation of the edges" against Python, and a divergence would have two
//! suspects instead of one.
//!
//! ## Zero-copy, stated honestly
//!
//! `score_batch` takes the numeric columns — timestamps, amounts, declined
//! flags — as `PyReadonlyArray1`, which borrows the NumPy buffer without
//! copying. The six identifier columns are Python strings; there is no such
//! thing as borrowing a `list[str]` zero-copy into Rust `&str`s across a batch
//! call, so those are converted (one allocation per string). The benchmark
//! reports the conversion share rather than hiding it: "zero-copy" here means
//! *the numeric columns*, and the docs say so wherever the word appears.

use numpy::{IntoPyArray, PyArray1, PyReadonlyArray1};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyList;

use crate::ingest::{Event, Status};
use crate::score::{Detector as CoreDetector, DetectorConfig};

/// Streaming detector, Python-facing.
///
/// Mirrors `spandan.detect.ReferenceDetector`'s surface: `update` per event,
/// `score_batch` for a stream, `reset`. Construction takes the same keyword
/// arguments as the Python `DetectorConfig`, with the same defaults, so the
/// eval harness can build either engine from one config dict.
#[pyclass(name = "Detector")]
pub struct PyDetector {
    core: CoreDetector,
}

#[allow(clippy::too_many_arguments)]
#[pymethods]
impl PyDetector {
    #[new]
    #[pyo3(signature = (
        window_ms=300_000,
        ring_capacity=512,
        baseline_sample_interval_ms=60_000,
        baseline_min_samples=20,
        ewma_halflife_samples=30.0,
        w_velocity_bin=1.0,
        w_decline_bin=2.0,
        w_amount=1.0,
        w_velocity_ip=0.5,
        w_repetition_damping=1.2,
        w_merchant_span_damping=1.4,
        threshold=3.0,
        use_ewma=true,
        use_per_ip=true,
    ))]
    fn new(
        window_ms: i64,
        ring_capacity: usize,
        baseline_sample_interval_ms: i64,
        baseline_min_samples: u64,
        ewma_halflife_samples: f64,
        w_velocity_bin: f64,
        w_decline_bin: f64,
        w_amount: f64,
        w_velocity_ip: f64,
        w_repetition_damping: f64,
        w_merchant_span_damping: f64,
        threshold: f64,
        use_ewma: bool,
        use_per_ip: bool,
    ) -> Self {
        PyDetector {
            core: CoreDetector::new(DetectorConfig {
                window_ms,
                ring_capacity,
                baseline_sample_interval_ms,
                baseline_min_samples,
                ewma_halflife_samples,
                w_velocity_bin,
                w_decline_bin,
                w_amount,
                w_velocity_ip,
                w_repetition_damping,
                w_merchant_span_damping,
                threshold,
                use_ewma,
                use_per_ip,
            }),
        }
    }

    /// Feed one event; returns its score.
    #[allow(clippy::too_many_arguments)]
    fn update(
        &mut self,
        ts: i64,
        txn_id: &str,
        merchant_id: &str,
        bin: &str,
        card_ref: &str,
        ip: &str,
        device_id: &str,
        amount_paise: i64,
        status: &str,
    ) -> PyResult<f64> {
        let event = Event {
            ts,
            txn_id: txn_id.to_string(),
            merchant_id: merchant_id.to_string(),
            bin: bin.to_string(),
            card_ref: card_ref.to_string(),
            ip: ip.to_string(),
            device_id: device_id.to_string(),
            amount_paise,
            status: Status::parse(status).map_err(|e| PyValueError::new_err(e.to_string()))?,
        };
        Ok(self.core.update(&event).0)
    }

    /// Score a whole stream, columnar.
    ///
    /// `ts`, `amount_paise` and `declined` are borrowed zero-copy from NumPy;
    /// the identifier columns are converted. Returns one score per event as a
    /// new NumPy array. The GIL is released while the core runs.
    #[allow(clippy::too_many_arguments)]
    fn score_batch<'py>(
        &mut self,
        py: Python<'py>,
        ts: PyReadonlyArray1<'py, i64>,
        amount_paise: PyReadonlyArray1<'py, i64>,
        declined: PyReadonlyArray1<'py, bool>,
        txn_id: &Bound<'py, PyList>,
        merchant_id: &Bound<'py, PyList>,
        bin: &Bound<'py, PyList>,
        card_ref: &Bound<'py, PyList>,
        ip: &Bound<'py, PyList>,
        device_id: &Bound<'py, PyList>,
    ) -> PyResult<Bound<'py, PyArray1<f64>>> {
        let ts = ts.as_slice()?;
        let amounts = amount_paise.as_slice()?;
        let declined = declined.as_slice()?;
        let n = ts.len();
        for (name, len) in [
            ("amount_paise", amounts.len()),
            ("declined", declined.len()),
            ("txn_id", txn_id.len()),
            ("merchant_id", merchant_id.len()),
            ("bin", bin.len()),
            ("card_ref", card_ref.len()),
            ("ip", ip.len()),
            ("device_id", device_id.len()),
        ] {
            if len != n {
                return Err(PyValueError::new_err(format!(
                    "column {name} has {len} rows, ts has {n}"
                )));
            }
        }

        // The unavoidable copy: Python strings to owned Rust strings. Counted
        // in the benchmark as part of the Rust engine's cost, not excused.
        let column = |list: &Bound<'py, PyList>| -> PyResult<Vec<String>> {
            list.iter().map(|item| item.extract::<String>()).collect()
        };
        let txn_ids = column(txn_id)?;
        let merchants = column(merchant_id)?;
        let bins = column(bin)?;
        let cards = column(card_ref)?;
        let ips = column(ip)?;
        let devices = column(device_id)?;

        let events: Vec<Event> = (0..n)
            .map(|i| Event {
                ts: ts[i],
                txn_id: txn_ids[i].clone(),
                merchant_id: merchants[i].clone(),
                bin: bins[i].clone(),
                card_ref: cards[i].clone(),
                ip: ips[i].clone(),
                device_id: devices[i].clone(),
                amount_paise: amounts[i],
                status: if declined[i] { Status::Declined } else { Status::Approved },
            })
            .collect();

        let scores = py.detach(|| self.core.score_batch(&events));
        Ok(scores.into_pyarray(py))
    }

    fn reset(&mut self) {
        self.core.reset();
    }

    /// Distinct entities currently tracked, across all four axes. Exposed so
    /// the memory measurement can report bytes per entity rather than guessing.
    fn entity_count(&self) -> usize {
        self.core.store().entity_count()
    }

    /// Events retained across all windows (bounded by entities x ring capacity).
    fn buffered_events(&self) -> usize {
        self.core.store().buffered_events()
    }

    /// Entities whose ring has hit its capacity bound at least once.
    fn saturated_entities(&self) -> usize {
        self.core.store().saturated_entities()
    }

    fn __repr__(&self) -> String {
        let cfg = self.core.config();
        format!(
            "spandan_core.Detector(window_ms={}, ring_capacity={}, threshold={}, \
             use_ewma={}, use_per_ip={}, entities={})",
            cfg.window_ms,
            cfg.ring_capacity,
            cfg.threshold,
            cfg.use_ewma,
            cfg.use_per_ip,
            self.core.store().entity_count()
        )
    }
}
