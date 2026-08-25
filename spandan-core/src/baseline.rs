//! Stage 4 of 5 — per-entity baselines.
//!
//! Two estimators, both streaming and both O(1) in memory:
//!
//! - [`Welford`] for mean and variance. Chosen over the naive
//!   `E[x²] − E[x]²` because that form loses catastrophic precision when the
//!   values sit far from zero, which per-entity window counts do as soon as an
//!   entity gets busy.
//! - [`Ewma`] for a recency-weighted mean, over *samples* rather than over time.
//!
//! Both must match `python/spandan/detect/reference.py` operation for operation,
//! not merely in the limit. The parity fixture compares scores that these feed,
//! so a different-but-equivalent formulation here shows up as a parity failure
//! with no obvious cause.

/// Streaming mean and variance, Welford's method.
#[derive(Clone, Debug, Default)]
pub struct Welford {
    count: u64,
    mean: f64,
    m2: f64,
}

impl Welford {
    pub fn new() -> Self {
        Self::default()
    }

    /// Fold one sample in.
    ///
    /// The update order — increment, delta, mean, then m2 against the *new*
    /// mean — is the same as the reference. Reordering these gives an
    /// arithmetically equivalent result that differs in the last bits.
    pub fn update(&mut self, value: f64) {
        self.count += 1;
        let delta = value - self.mean;
        self.mean += delta / self.count as f64;
        self.m2 += delta * (value - self.mean);
    }

    #[inline]
    pub fn count(&self) -> u64 {
        self.count
    }

    #[inline]
    pub fn mean(&self) -> f64 {
        self.mean
    }

    /// Sample variance. Zero until there are two samples — with one sample the
    /// quantity is undefined, and returning zero keeps the caller branch-free.
    pub fn variance(&self) -> f64 {
        if self.count > 1 {
            self.m2 / (self.count - 1) as f64
        } else {
            0.0
        }
    }

    pub fn stddev(&self) -> f64 {
        self.variance().sqrt()
    }
}

/// Exponentially weighted mean over samples (not over elapsed time).
#[derive(Clone, Debug)]
pub struct Ewma {
    alpha: f64,
    value: f64,
    count: u64,
}

impl Ewma {
    /// `halflife_samples` is how many samples it takes for a step change to be
    /// half absorbed.
    pub fn new(halflife_samples: f64) -> Self {
        let halflife = halflife_samples.max(1e-9);
        Ewma {
            alpha: 1.0 - (-std::f64::consts::LN_2 / halflife).exp(),
            value: 0.0,
            count: 0,
        }
    }

    /// The first sample *sets* the value rather than being blended into zero.
    /// Blending from zero would make every entity look like it was ramping up
    /// from nothing for its first few windows.
    pub fn update(&mut self, sample: f64) {
        if self.count == 0 {
            self.value = sample;
        } else {
            self.value += self.alpha * (sample - self.value);
        }
        self.count += 1;
    }

    #[inline]
    pub fn value(&self) -> f64 {
        self.value
    }

    #[inline]
    pub fn count(&self) -> u64 {
        self.count
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn two_pass(values: &[f64]) -> (f64, f64) {
        let n = values.len() as f64;
        let mean = values.iter().sum::<f64>() / n;
        let variance = values.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / (n - 1.0);
        (mean, variance)
    }

    #[test]
    fn welford_is_empty_until_fed() {
        let w = Welford::new();
        assert_eq!(w.count(), 0);
        assert_eq!(w.mean(), 0.0);
        assert_eq!(w.variance(), 0.0);
        assert_eq!(w.stddev(), 0.0);
    }

    #[test]
    fn welford_variance_of_one_sample_is_zero_not_nan() {
        let mut w = Welford::new();
        w.update(42.0);
        assert_eq!(w.count(), 1);
        assert_eq!(w.mean(), 42.0);
        assert_eq!(w.variance(), 0.0);
    }

    #[test]
    fn welford_survives_a_large_offset_where_naive_variance_fails() {
        // The reason this estimator is here rather than sum-of-squares. Values
        // near 1e9 with unit spread annihilate a naive E[x^2] - E[x]^2.
        let values: Vec<f64> = (0..5_000)
            .map(|i| 1e9 + ((i % 7) as f64 - 3.0))
            .collect();
        let mut w = Welford::new();
        for v in &values {
            w.update(*v);
        }
        let (mean, variance) = two_pass(&values);
        assert!((w.mean() - mean).abs() < 1e-6);
        assert!((w.variance() - variance).abs() / variance < 1e-6);
        assert!(w.variance() > 0.0, "variance collapsed to zero");
    }

    #[test]
    fn ewma_tracks_a_constant_exactly() {
        let mut e = Ewma::new(30.0);
        for _ in 0..100 {
            e.update(7.5);
        }
        assert!((e.value() - 7.5).abs() < 1e-12);
    }

    #[test]
    fn ewma_first_sample_sets_rather_than_blends() {
        let mut e = Ewma::new(30.0);
        e.update(100.0);
        assert_eq!(e.value(), 100.0);
    }

    proptest! {
        /// PHASES.md names this: `baseline::tests::welford_matches_two_pass_within_tol`
        #[test]
        fn welford_matches_two_pass_within_tol(
            values in proptest::collection::vec(-1e6f64..1e6f64, 2..400)
        ) {
            let mut w = Welford::new();
            for v in &values {
                w.update(*v);
            }
            let (mean, variance) = two_pass(&values);

            let scale = values.iter().fold(1.0f64, |acc, v| acc.max(v.abs()));
            prop_assert!((w.mean() - mean).abs() <= 1e-9 * scale.max(1.0));
            prop_assert!((w.variance() - variance).abs() <= 1e-6 * variance.max(1.0));
        }

        /// PHASES.md names this: `baseline::tests::ewma_bounded_by_input_extremes`
        #[test]
        fn ewma_bounded_by_input_extremes(
            values in proptest::collection::vec(-1e5f64..1e5f64, 1..300),
            halflife in 1.0f64..200.0,
        ) {
            let mut e = Ewma::new(halflife);
            for v in &values {
                e.update(*v);
            }
            let lo = values.iter().cloned().fold(f64::INFINITY, f64::min);
            let hi = values.iter().cloned().fold(f64::NEG_INFINITY, f64::max);

            // A weighted mean of the inputs cannot leave their range. A sign or
            // alpha error would show up here immediately.
            prop_assert!(e.value() >= lo - 1e-9, "{} below min {}", e.value(), lo);
            prop_assert!(e.value() <= hi + 1e-9, "{} above max {}", e.value(), hi);
        }

        #[test]
        fn welford_is_order_independent_in_the_mean(
            values in proptest::collection::vec(-1e4f64..1e4f64, 2..200)
        ) {
            let mut forward = Welford::new();
            for v in &values {
                forward.update(*v);
            }
            let mut backward = Welford::new();
            for v in values.iter().rev() {
                backward.update(*v);
            }
            let scale = values.iter().fold(1.0f64, |acc, v| acc.max(v.abs()));
            prop_assert!((forward.mean() - backward.mean()).abs() <= 1e-9 * scale);
        }
    }
}
