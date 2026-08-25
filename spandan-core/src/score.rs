//! Stage 5 of 5 — the score, and the detector that drives the other four.
//!
//! The score is a **deviation from a per-entity baseline, not a probability.**
//! That is why the evaluation reports a cost-vs-threshold sweep rather than a
//! calibration curve: asking whether 0.7 "means" 70% asks something the quantity
//! cannot answer.
//!
//! ## Evidence, then damping
//!
//! Positive evidence that this looks like card testing:
//!
//! - `velocity_bin` — window count on this BIN, in standard deviations above the
//!   BIN's own baseline.
//! - `decline_bin`  — window decline ratio above the BIN's baseline ratio. The
//!   primary signal.
//! - `amount`       — how far below baseline the window's mean amount sits.
//!   Probes are small; ordinary baskets are not.
//! - `velocity_ip`  — the same velocity term on the IP axis. Catches a
//!   single-address burst and, by construction, misses a rotating one.
//!
//! Subtracted, because these are what a legitimate issuer outage looks like:
//!
//! - `repetition`    — events per distinct card *inside the current window*.
//! - `merchant_span` — distinct merchants for this BIN inside the window.
//!
//! `repetition` is the closest thing here to a banned feature, so to be explicit:
//! it is computed only from cards present in the current window and never
//! consults whether a card was seen before. It would behave identically on a
//! stream where every card had appeared a thousand times already. It measures
//! retry behaviour, not novelty.
//!
//! ## Summation order is part of the contract
//!
//! The six terms are summed in the order listed above, matching the reference's
//! insertion order. Floating-point addition is not associative, so a different
//! order gives an arithmetically equivalent score that differs in the last bits
//! — and the parity fixture compares to 1e-9.

use crate::baseline::Welford;
use crate::ingest::Event;
use crate::state::{Axis, StateStore};

/// Every tunable. Mirrors `spandan.detect.interface.DetectorConfig`.
///
/// Frozen for Phase 3: the Rust core is tested for numerical parity against the
/// reference, so these values are the contract, not preferences.
#[derive(Clone, Debug)]
pub struct DetectorConfig {
    pub window_ms: i64,
    pub ring_capacity: usize,
    pub baseline_sample_interval_ms: i64,
    pub baseline_min_samples: u64,
    pub ewma_halflife_samples: f64,

    pub w_velocity_bin: f64,
    pub w_decline_bin: f64,
    pub w_amount: f64,
    pub w_velocity_ip: f64,
    pub w_repetition_damping: f64,
    pub w_merchant_span_damping: f64,

    pub threshold: f64,
    pub use_ewma: bool,
    pub use_per_ip: bool,
}

impl Default for DetectorConfig {
    fn default() -> Self {
        DetectorConfig {
            window_ms: 300_000,
            ring_capacity: 512,
            baseline_sample_interval_ms: 60_000,
            baseline_min_samples: 20,
            ewma_halflife_samples: 30.0,
            w_velocity_bin: 1.0,
            w_decline_bin: 2.0,
            w_amount: 1.0,
            w_velocity_ip: 0.5,
            w_repetition_damping: 1.2,
            w_merchant_span_damping: 1.4,
            threshold: 3.0,
            use_ewma: true,
            use_per_ip: true,
        }
    }
}

/// The six contributions, in summation order. Kept so an explanation can never
/// assert a cause the arithmetic does not support.
#[derive(Clone, Copy, Debug, Default)]
pub struct Contributions {
    pub velocity_bin: f64,
    pub decline_bin: f64,
    pub amount: f64,
    pub velocity_ip: f64,
    pub repetition: f64,
    pub merchant_span: f64,
}

impl Contributions {
    pub fn total(&self) -> f64 {
        self.velocity_bin
            + self.decline_bin
            + self.amount
            + self.velocity_ip
            + self.repetition
            + self.merchant_span
    }
}

/// A scored event that cleared the threshold.
#[derive(Clone, Debug)]
pub struct Flag {
    pub ts: i64,
    pub txn_id: String,
    pub merchant_id: String,
    pub bin: String,
    pub score: f64,
    pub threshold: f64,
    pub window_events: usize,
    pub window_declines: i64,
    pub window_decline_ratio: f64,
    pub baseline_decline_ratio: f64,
    pub velocity_z: f64,
    pub window_distinct_cards: usize,
    pub window_distinct_merchants: usize,
    pub window_saturated: bool,
    pub contributions: Contributions,
}

pub struct Detector {
    config: DetectorConfig,
    store: StateStore,
    global_count: Welford,
    last_global_ms: i64,
}

const NEVER: i64 = i64::MIN / 2;

impl Detector {
    pub fn new(config: DetectorConfig) -> Self {
        Detector {
            config,
            store: StateStore::new(),
            global_count: Welford::new(),
            last_global_ms: NEVER,
        }
    }

    pub fn config(&self) -> &DetectorConfig {
        &self.config
    }

    pub fn store(&self) -> &StateStore {
        &self.store
    }

    pub fn reset(&mut self) {
        self.store.clear();
        self.global_count = Welford::new();
        self.last_global_ms = NEVER;
    }

    /// Window count in standard deviations above this entity's baseline.
    ///
    /// With `use_ewma` off the comparison is against a single global mean
    /// instead — that is the drop-EWMA ablation, and what a detector with no
    /// per-entity memory would have to do.
    fn velocity_z(&self, axis: Axis, key: &str) -> f64 {
        let Some(state) = self.store.get(axis, key) else {
            return 0.0;
        };
        let count = state.window.len() as f64;

        let (centre, spread) = if self.config.use_ewma {
            if state.baseline_count.count() < self.config.baseline_min_samples {
                return 0.0;
            }
            (state.ewma_count.value(), state.baseline_count.stddev())
        } else {
            if self.global_count.count() < self.config.baseline_min_samples {
                return 0.0;
            }
            (self.global_count.mean(), self.global_count.stddev())
        };

        if spread <= 1e-9 {
            return 0.0;
        }
        (count - centre) / spread
    }

    /// Feed one event and return its score, plus the evidence behind it.
    pub fn update(&mut self, event: &Event) -> (f64, Flag) {
        let cfg_window = self.config.window_ms;
        let cutoff = event.ts - cfg_window;
        let declined = event.status.is_declined();
        let amount = event.amount_paise as f64;
        let capacity = self.config.ring_capacity;
        let halflife = self.config.ewma_halflife_samples;

        // Advance every axis before scoring: the current event is inside its own
        // window, per the (t - W, t] convention.
        for axis in Axis::ALL {
            let key = axis_key(axis, event);
            let state = self.store.entry(axis, key, capacity, halflife);
            state.window.evict_before(cutoff);
            state
                .window
                .push(event.ts, declined, amount, &event.card_ref, &event.merchant_id);
        }

        let (score, contributions, flag) = self.score(event);

        // Baselines fold AFTER scoring, so an event is never compared against a
        // baseline that already contains it.
        for axis in Axis::ALL {
            let key = axis_key(axis, event).to_string();
            let interval = self.config.baseline_sample_interval_ms;
            if let Some(state) = self.store.map_entry(axis, &key) {
                state.fold_baseline(event.ts, interval);
            }
        }
        if event.ts - self.last_global_ms >= self.config.baseline_sample_interval_ms {
            self.last_global_ms = event.ts;
            let bin_len = self
                .store
                .get(Axis::Bin, &event.bin)
                .map(|s| s.window.len())
                .unwrap_or(0);
            self.global_count.update(bin_len as f64);
        }

        let _ = contributions;
        (score, flag)
    }

    fn score(&self, event: &Event) -> (f64, Contributions, Flag) {
        let cfg = &self.config;
        let bin = self
            .store
            .get(Axis::Bin, &event.bin)
            .expect("bin state was just created");

        let count = bin.window.len();
        let decline_ratio = bin.window.decline_ratio();
        let baseline_decline = if bin.baseline_declines.count() >= cfg.baseline_min_samples {
            bin.baseline_declines.mean()
        } else {
            0.0
        };
        let amount_mean = bin.window.mean_amount();
        let baseline_amount = if bin.baseline_amount.count() >= cfg.baseline_min_samples {
            bin.baseline_amount.mean()
        } else {
            0.0
        };

        let distinct_cards = bin.window.distinct_cards();
        let distinct_merchants = bin.window.distinct_merchants();
        let cards_per_event = if count > 0 {
            distinct_cards as f64 / count as f64
        } else {
            1.0
        };

        let warm = bin.baseline_count.count() >= cfg.baseline_min_samples || !cfg.use_ewma;

        let velocity_bin = self.velocity_z(Axis::Bin, &event.bin).max(0.0);
        let decline_excess = if warm {
            (decline_ratio - baseline_decline).max(0.0)
        } else {
            0.0
        };
        let amount_term = if warm && baseline_amount > 0.0 && amount_mean > 0.0 {
            (baseline_amount / amount_mean).ln().max(0.0)
        } else {
            0.0
        };
        let velocity_ip = if cfg.use_per_ip {
            self.velocity_z(Axis::Ip, &event.ip).max(0.0)
        } else {
            0.0
        };

        let repetition = if count > 0 {
            (1.0 / cards_per_event.max(1e-9) - 1.0).max(0.0)
        } else {
            0.0
        };
        let span = (distinct_merchants as f64 - 1.0).max(0.0);

        // Order matters. See the module docs.
        let contributions = Contributions {
            velocity_bin: cfg.w_velocity_bin * velocity_bin,
            decline_bin: cfg.w_decline_bin * decline_excess * 10.0,
            amount: cfg.w_amount * amount_term,
            velocity_ip: cfg.w_velocity_ip * velocity_ip,
            repetition: -cfg.w_repetition_damping * repetition,
            merchant_span: -cfg.w_merchant_span_damping * span,
        };
        let score = contributions.total();

        let flag = Flag {
            ts: event.ts,
            txn_id: event.txn_id.clone(),
            merchant_id: event.merchant_id.clone(),
            bin: event.bin.clone(),
            score,
            threshold: cfg.threshold,
            window_events: count,
            window_declines: bin.window.declines(),
            window_decline_ratio: decline_ratio,
            baseline_decline_ratio: baseline_decline,
            velocity_z: velocity_bin,
            window_distinct_cards: distinct_cards,
            window_distinct_merchants: distinct_merchants,
            window_saturated: bin.window.saturated(),
            contributions,
        };
        (score, contributions, flag)
    }

    /// Score a whole stream. Identical arithmetic to repeated `update`.
    pub fn score_batch(&mut self, events: &[Event]) -> Vec<f64> {
        events.iter().map(|e| self.update(e).0).collect()
    }
}

fn axis_key(axis: Axis, event: &Event) -> &str {
    match axis {
        Axis::Bin => &event.bin,
        Axis::Ip => &event.ip,
        Axis::Device => &event.device_id,
        Axis::Merchant => &event.merchant_id,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ingest::Status;

    fn event(ts: i64, bin: &str, declined: bool) -> Event {
        Event {
            ts,
            txn_id: format!("txn_{ts}"),
            merchant_id: "mer_000".into(),
            bin: bin.into(),
            card_ref: format!("card_{ts}"),
            ip: "192.0.2.1".into(),
            device_id: "dev_1".into(),
            amount_paise: 15_000,
            status: if declined { Status::Declined } else { Status::Approved },
        }
    }

    #[test]
    fn contributions_sum_to_the_score() {
        let mut detector = Detector::new(DetectorConfig::default());
        for i in 0..500 {
            let (score, flag) = detector.update(&event(i * 1_000, "000111", i % 3 == 0));
            assert!(
                (flag.contributions.total() - score).abs() < 1e-12,
                "terms do not sum to the score"
            );
        }
    }

    #[test]
    fn cold_entities_score_zero_rather_than_infinity() {
        // With no baseline there is nothing to deviate from. A cold entity is
        // not evidence; it is just cold.
        let mut detector = Detector::new(DetectorConfig::default());
        let (score, _) = detector.update(&event(0, "000111", true));
        assert_eq!(score, 0.0);
    }

    #[test]
    fn deterministic_across_runs() {
        let events: Vec<Event> = (0..800)
            .map(|i| event(i * 700, if i % 4 == 0 { "000111" } else { "000222" }, i % 5 < 2))
            .collect();
        let a = Detector::new(DetectorConfig::default()).score_batch(&events);
        let b = Detector::new(DetectorConfig::default()).score_batch(&events);
        assert_eq!(a, b);
    }

    #[test]
    fn streaming_matches_batch() {
        let events: Vec<Event> = (0..600)
            .map(|i| event(i * 900, "000111", i % 3 == 0))
            .collect();
        let batch = Detector::new(DetectorConfig::default()).score_batch(&events);

        let mut streamed = Vec::new();
        let mut detector = Detector::new(DetectorConfig::default());
        for e in &events {
            streamed.push(detector.update(e).0);
        }
        assert_eq!(batch, streamed);
    }

    #[test]
    fn reset_returns_the_detector_to_cold() {
        let mut detector = Detector::new(DetectorConfig::default());
        for i in 0..300 {
            detector.update(&event(i * 1_000, "000111", true));
        }
        assert!(detector.store().entity_count() > 0);
        detector.reset();
        assert_eq!(detector.store().entity_count(), 0);
        assert_eq!(detector.update(&event(0, "000111", true)).0, 0.0);
    }
}
