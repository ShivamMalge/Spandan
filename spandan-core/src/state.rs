//! Stage 2 of 5 — per-entity state, and the store that holds it.
//!
//! One [`EntityState`] per (axis, identifier). The store grows with the number
//! of *distinct entities seen*, never with the number of events processed —
//! `window_memory_bounded_per_entity` asserts exactly that and no more. Note
//! what the claim is NOT: total memory is **linear in entity count**, because
//! entities are never freed. Per-entity cost and a realistic monthly projection
//! are measured in `docs/BENCH.md`; the unbuilt fix is FAILURE_MODES §7.
//!
//! Card is deliberately absent from [`Axis`]. `gen/ASSUMPTIONS.md` §1.7a forbids
//! any feature derived from card novelty or first-seen-ness, because the
//! flash-sale control only partially controls for novelty. Cards are counted
//! *within a window* to measure repetition, which needs no history and is a
//! different quantity.

use std::collections::HashMap;

use crate::baseline::{Ewma, Welford};
use crate::velocity::Window;

/// The entity axes the detector keys on.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub enum Axis {
    Bin,
    Ip,
    Device,
    Merchant,
}

impl Axis {
    pub const ALL: [Axis; 4] = [Axis::Bin, Axis::Ip, Axis::Device, Axis::Merchant];

    /// Only the BIN axis reads distinct-entity counts. See [`Window::new`].
    #[inline]
    pub fn tracks_distinct(self) -> bool {
        matches!(self, Axis::Bin)
    }
}

/// Sliding window plus baselines for one entity.
#[derive(Clone, Debug)]
pub struct EntityState {
    pub window: Window,
    pub baseline_count: Welford,
    pub baseline_declines: Welford,
    pub baseline_amount: Welford,
    pub ewma_count: Ewma,
    last_baseline_ms: i64,
}

/// Sentinel meaning "no baseline sample has ever been folded".
///
/// Far enough below any real timestamp that the first fold always passes the
/// interval gate without a special case.
const NEVER: i64 = i64::MIN / 2;

impl EntityState {
    pub fn new(capacity: usize, halflife: f64, track_distinct: bool) -> Self {
        EntityState {
            window: Window::new(capacity, track_distinct),
            baseline_count: Welford::new(),
            baseline_declines: Welford::new(),
            baseline_amount: Welford::new(),
            ewma_count: Ewma::new(halflife),
            last_baseline_ms: NEVER,
        }
    }

    /// Fold the current window into the baselines, at most once per interval.
    ///
    /// The gate is load-bearing, not an optimisation. Folding on every event
    /// would let a burst pour hundreds of inflated samples into the very
    /// baseline it is being compared against, and it would partly conceal
    /// itself. The reference does the same, so this is parity-relevant.
    pub fn fold_baseline(&mut self, ts: i64, interval_ms: i64) {
        if ts - self.last_baseline_ms < interval_ms {
            return;
        }
        self.last_baseline_ms = ts;

        let count = self.window.len();
        self.baseline_count.update(count as f64);
        self.ewma_count.update(count as f64);
        if count > 0 {
            self.baseline_declines.update(self.window.decline_ratio());
            self.baseline_amount.update(self.window.mean_amount());
        }
    }
}

/// Per-axis entity stores.
#[derive(Debug, Default)]
pub struct StateStore {
    bins: HashMap<String, EntityState>,
    ips: HashMap<String, EntityState>,
    devices: HashMap<String, EntityState>,
    merchants: HashMap<String, EntityState>,
}

impl StateStore {
    pub fn new() -> Self {
        Self::default()
    }

    fn map_mut(&mut self, axis: Axis) -> &mut HashMap<String, EntityState> {
        match axis {
            Axis::Bin => &mut self.bins,
            Axis::Ip => &mut self.ips,
            Axis::Device => &mut self.devices,
            Axis::Merchant => &mut self.merchants,
        }
    }

    /// Fetch or create the state for one entity.
    pub fn entry(
        &mut self,
        axis: Axis,
        key: &str,
        capacity: usize,
        halflife: f64,
    ) -> &mut EntityState {
        let track = axis.tracks_distinct();
        self.map_mut(axis)
            .entry(key.to_string())
            .or_insert_with(|| EntityState::new(capacity, halflife, track))
    }

    /// Mutable access for the baseline fold, which happens after scoring.
    pub fn map_entry(&mut self, axis: Axis, key: &str) -> Option<&mut EntityState> {
        self.map_mut(axis).get_mut(key)
    }

    pub fn get(&self, axis: Axis, key: &str) -> Option<&EntityState> {
        match axis {
            Axis::Bin => self.bins.get(key),
            Axis::Ip => self.ips.get(key),
            Axis::Device => self.devices.get(key),
            Axis::Merchant => self.merchants.get(key),
        }
    }

    /// Distinct entities tracked across all axes.
    pub fn entity_count(&self) -> usize {
        self.bins.len() + self.ips.len() + self.devices.len() + self.merchants.len()
    }

    /// Events currently retained across every window. Bounded by
    /// `entity_count() * ring_capacity`, never by the number of events seen.
    pub fn buffered_events(&self) -> usize {
        let sum = |m: &HashMap<String, EntityState>| m.values().map(|s| s.window.len()).sum::<usize>();
        sum(&self.bins) + sum(&self.ips) + sum(&self.devices) + sum(&self.merchants)
    }

    /// Windows that have hit their capacity bound at least once. Used by the
    /// parity test to assert the fixture exercises ring wraparound - the Phase 3
    /// review found the original fixture never filled a ring, so parity had
    /// verified only the happy path.
    pub fn saturated_entities(&self) -> usize {
        let count = |m: &HashMap<String, EntityState>| {
            m.values().filter(|s| s.window.saturated()).count()
        };
        count(&self.bins) + count(&self.ips) + count(&self.devices) + count(&self.merchants)
    }

    pub fn clear(&mut self) {
        self.bins.clear();
        self.ips.clear();
        self.devices.clear();
        self.merchants.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn card_is_not_an_axis() {
        // The binding constraint from gen/ASSUMPTIONS.md 1.7a, enforced by the
        // type system rather than by remembering it.
        assert_eq!(Axis::ALL.len(), 4);
        assert!(Axis::ALL.contains(&Axis::Bin));
        assert!(Axis::ALL.contains(&Axis::Merchant));
        // There is deliberately no Axis::Card to assert the absence of.
    }

    #[test]
    fn only_bin_tracks_distinct_entities() {
        assert!(Axis::Bin.tracks_distinct());
        assert!(!Axis::Ip.tracks_distinct());
        assert!(!Axis::Device.tracks_distinct());
        assert!(!Axis::Merchant.tracks_distinct());
    }

    #[test]
    fn window_memory_bounded_per_entity() {
        // Renamed at the Phase 3 gate from `memory_bounded_under_entity_churn`,
        // which asserted something narrower than its name claimed. What IS
        // bounded: retained events per entity (the ring capacity). What is NOT:
        // total memory, which is linear in distinct entity count - entities are
        // never freed. A month of real merchant traffic brings millions of
        // distinct IPs, each allocating ring buffers that live forever. That is
        // measured (bytes/entity and a monthly projection) in docs/BENCH.md, and
        // the unbuilt fix (LRU eviction or a count-min sketch) is recorded in
        // docs/FAILURE_MODES.md 7.
        const CAPACITY: usize = 16;
        let mut store = StateStore::new();

        let events = 20_000;
        for i in 0..events {
            // 200 distinct BINs, cycled - heavy churn, bounded entity count.
            let key = format!("bin_{}", i % 200);
            let state = store.entry(Axis::Bin, &key, CAPACITY, 30.0);
            state.window.push(i as i64, i % 3 == 0, 100.0, "card", "mer");
        }

        assert_eq!(store.entity_count(), 200);
        assert!(
            store.buffered_events() <= store.entity_count() * CAPACITY,
            "buffered {} exceeds the {} bound",
            store.buffered_events(),
            store.entity_count() * CAPACITY
        );
        assert!(
            store.buffered_events() < events,
            "state retained {} of {} events - that is not bounded memory",
            store.buffered_events(),
            events
        );
    }

    #[test]
    fn baseline_fold_is_gated_by_the_interval() {
        let mut state = EntityState::new(64, 30.0, true);
        state.window.push(0, false, 100.0, "c", "m");

        state.fold_baseline(0, 60_000);
        assert_eq!(state.baseline_count.count(), 1);

        // Inside the interval: refused, so a burst cannot flood its own baseline.
        state.fold_baseline(30_000, 60_000);
        assert_eq!(state.baseline_count.count(), 1);

        state.fold_baseline(60_000, 60_000);
        assert_eq!(state.baseline_count.count(), 2);
    }

    #[test]
    fn axes_do_not_share_a_namespace() {
        // An IP and a BIN with the same literal id must not collide.
        let mut store = StateStore::new();
        store.entry(Axis::Bin, "shared", 8, 30.0).window.push(1, false, 1.0, "c", "m");
        store.entry(Axis::Ip, "shared", 8, 30.0).window.push(2, true, 2.0, "c", "m");

        assert_eq!(store.get(Axis::Bin, "shared").unwrap().window.len(), 1);
        assert_eq!(store.get(Axis::Ip, "shared").unwrap().window.declines(), 1);
        assert_eq!(store.entity_count(), 2);
    }
}
