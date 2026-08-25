//! Stage 3 of 5 — the sliding window.
//!
//! A fixed-capacity ring buffer per entity, plus the aggregates the score needs,
//! maintained incrementally on push and eviction rather than recomputed by
//! walking the window.
//!
//! ## The window convention
//!
//! A window of width `W` evaluated at time `t` covers the half-open interval
//! `(t − W, t]`. The current event is always inside its own window; an event
//! exactly `W` old has fallen out. This is written down in
//! `python/spandan/detect/interface.py` and repeated here because a
//! half-open/closed disagreement between two implementations is the classic
//! parity day-eater — it produces small, plausible, intermittent differences
//! rather than an obvious break.
//!
//! ## Why capacity is fixed
//!
//! Memory per entity is bounded no matter how hot the entity gets. That is what
//! lets this be called bounded-memory streaming rather than fast batch scoring.
//! When the ring is full the oldest retained event is dropped to make room and
//! the entity is marked saturated, so the loss is recorded rather than silent.

use std::collections::HashMap;
use std::collections::VecDeque;

/// One retained event, trimmed to what the aggregates need.
#[derive(Clone, Debug)]
struct Slot {
    ts: i64,
    declined: bool,
    amount: f64,
    card: String,
    merchant: String,
}

/// Sliding window over one entity's recent events.
#[derive(Clone, Debug)]
pub struct Window {
    events: VecDeque<Slot>,
    capacity: usize,
    declines: i64,
    amount_sum: f64,
    track_distinct: bool,
    card_counts: HashMap<String, u32>,
    merchant_counts: HashMap<String, u32>,
    saturated: bool,
}

impl Window {
    /// `track_distinct` is false for every axis except BIN.
    ///
    /// Only the BIN axis reads distinct-card and distinct-merchant counts, and
    /// maintaining the two maps on all four axes costs real throughput to answer
    /// a question three of them never ask. The reference makes the same
    /// distinction, so this is parity-relevant as well as a speed choice.
    pub fn new(capacity: usize, track_distinct: bool) -> Self {
        Window {
            events: VecDeque::with_capacity(capacity.min(64)),
            capacity,
            declines: 0,
            amount_sum: 0.0,
            track_distinct,
            card_counts: HashMap::new(),
            merchant_counts: HashMap::new(),
            saturated: false,
        }
    }

    fn forget(&mut self, slot: &Slot) {
        self.declines -= slot.declined as i64;
        self.amount_sum -= slot.amount;
        if !self.track_distinct {
            return;
        }
        for (table, key) in [
            (&mut self.card_counts, &slot.card),
            (&mut self.merchant_counts, &slot.merchant),
        ] {
            if let Some(remaining) = table.get_mut(key) {
                *remaining -= 1;
                if *remaining == 0 {
                    table.remove(key);
                }
            }
        }
    }

    /// Drop everything at or before `cutoff`. Window is `(t − W, t]`.
    pub fn evict_before(&mut self, cutoff_ms: i64) {
        while let Some(front) = self.events.front() {
            if front.ts > cutoff_ms {
                break;
            }
            let slot = self.events.pop_front().expect("front exists");
            self.forget(&slot);
        }
    }

    pub fn push(
        &mut self,
        ts: i64,
        declined: bool,
        amount: f64,
        card: &str,
        merchant: &str,
    ) {
        if self.events.len() == self.capacity {
            let slot = self.events.pop_front().expect("full ring has a front");
            self.forget(&slot);
            self.saturated = true;
        }
        self.events.push_back(Slot {
            ts,
            declined,
            amount,
            card: card.to_string(),
            merchant: merchant.to_string(),
        });
        self.declines += declined as i64;
        self.amount_sum += amount;
        if self.track_distinct {
            *self.card_counts.entry(card.to_string()).or_insert(0) += 1;
            *self.merchant_counts.entry(merchant.to_string()).or_insert(0) += 1;
        }
    }

    #[inline]
    pub fn len(&self) -> usize {
        self.events.len()
    }

    #[inline]
    pub fn is_empty(&self) -> bool {
        self.events.is_empty()
    }

    #[inline]
    pub fn declines(&self) -> i64 {
        self.declines
    }

    #[inline]
    pub fn amount_sum(&self) -> f64 {
        self.amount_sum
    }

    #[inline]
    pub fn distinct_cards(&self) -> usize {
        self.card_counts.len()
    }

    #[inline]
    pub fn distinct_merchants(&self) -> usize {
        self.merchant_counts.len()
    }

    #[inline]
    pub fn saturated(&self) -> bool {
        self.saturated
    }

    pub fn decline_ratio(&self) -> f64 {
        if self.events.is_empty() {
            0.0
        } else {
            self.declines as f64 / self.events.len() as f64
        }
    }

    pub fn mean_amount(&self) -> f64 {
        if self.events.is_empty() {
            0.0
        } else {
            self.amount_sum / self.events.len() as f64
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn window_count_matches_bruteforce() {
        // Recomputed from scratch rather than read off the incremental state, so
        // a bookkeeping bug in push/evict cannot hide behind its own counter.
        const W: i64 = 300_000;
        let stamps: Vec<i64> = (0..2_000).map(|i| i * 173).collect();

        let mut window = Window::new(100_000, true);
        for (i, ts) in stamps.iter().enumerate() {
            window.evict_before(ts - W);
            window.push(*ts, i % 3 == 0, 100.0, "card", "mer");

            if i % 97 == 0 {
                let expected = stamps[..=i].iter().filter(|s| **s > ts - W && **s <= *ts).count();
                assert_eq!(window.len(), expected, "window mismatch at event {i}");
            }
        }
    }

    #[test]
    fn event_exactly_one_window_old_has_fallen_out() {
        // Pins the half-open boundary. (t - W, t] keeps t, drops t - W.
        let mut window = Window::new(64, true);
        window.push(1_000, false, 100.0, "a", "m");
        window.push(2_000, false, 100.0, "b", "m");

        window.evict_before(2_000 - 1_000);
        assert_eq!(window.len(), 1);
        assert_eq!(window.events.front().unwrap().ts, 2_000);
    }

    #[test]
    fn decline_ratio_matches_bruteforce() {
        const W: i64 = 60_000;
        let mut window = Window::new(100_000, true);
        let declined: Vec<bool> = (0..500).map(|i| i % 5 < 2).collect();

        for (i, flag) in declined.iter().enumerate() {
            let ts = i as i64 * 500;
            window.evict_before(ts - W);
            window.push(ts, *flag, 10.0, "card", "mer");

            let expected = declined[..=i]
                .iter()
                .enumerate()
                .filter(|(j, _)| {
                    let jts = *j as i64 * 500;
                    jts > ts - W && jts <= ts
                })
                .filter(|(_, d)| **d)
                .count() as i64;
            assert_eq!(window.declines(), expected, "declines mismatch at {i}");
        }
    }

    #[test]
    fn distinct_counts_only_tracked_where_asked() {
        let mut off = Window::new(64, false);
        off.push(1, false, 1.0, "card_a", "mer_a");
        assert_eq!(off.distinct_cards(), 0, "untracked axis should not pay for maps");

        let mut on = Window::new(64, true);
        on.push(1, false, 1.0, "card_a", "mer_a");
        on.push(2, false, 1.0, "card_a", "mer_b");
        assert_eq!(on.distinct_cards(), 1);
        assert_eq!(on.distinct_merchants(), 2);
    }

    #[test]
    fn empty_window_reports_zero_not_nan() {
        let window = Window::new(8, true);
        assert_eq!(window.decline_ratio(), 0.0);
        assert_eq!(window.mean_amount(), 0.0);
    }

    proptest! {
        /// PHASES.md names this: `velocity::tests::ring_buffer_never_exceeds_capacity`
        #[test]
        fn ring_buffer_never_exceeds_capacity(
            capacity in 1usize..64,
            pushes in 1usize..2_000,
        ) {
            let mut window = Window::new(capacity, true);
            for i in 0..pushes {
                window.push(i as i64, i % 2 == 0, i as f64, &format!("card_{i}"), "mer");
                prop_assert!(window.len() <= capacity);
            }
            if pushes > capacity {
                prop_assert!(window.saturated(), "overflow must be recorded, not silent");
            }
        }

        /// Incremental aggregates must equal a fresh walk of the retained window.
        #[test]
        fn aggregates_agree_with_a_walk_of_the_window(
            capacity in 2usize..48,
            count in 1usize..400,
        ) {
            let mut window = Window::new(capacity, true);
            for i in 0..count {
                window.push(i as i64, i % 3 == 0, (i % 17) as f64, &format!("c{}", i % 11), "mer");
            }
            let walked_declines: i64 = window.events.iter().filter(|s| s.declined).count() as i64;
            let walked_sum: f64 = window.events.iter().map(|s| s.amount).sum();
            let walked_cards = window
                .events
                .iter()
                .map(|s| s.card.clone())
                .collect::<std::collections::HashSet<_>>()
                .len();

            prop_assert_eq!(window.declines(), walked_declines);
            prop_assert!((window.amount_sum() - walked_sum).abs() < 1e-9);
            prop_assert_eq!(window.distinct_cards(), walked_cards);
        }
    }
}
