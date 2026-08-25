//! Numerical parity against the frozen Python reference.
//!
//! This is the test PHASES.md Phase 3 says to write **first**, and it is the
//! reason the port means anything. Without a committed artifact stating what the
//! reference produces, "parity" degrades into running both implementations and
//! believing whichever looks right.
//!
//! ## What it replays
//!
//! `tests/fixtures/parity.tsv` — 2,966 events from a cold start, touching all six
//! generator scenarios, with one expected score per event. It is committed, so
//! `cargo test` runs in a fresh clone with no Python step and no generated data.
//!
//! The fixture also carries the exact `DetectorConfig` those scores were produced
//! under, and this test builds its detector from that rather than from
//! `DetectorConfig::default()`. A window-size or weight mismatch therefore fails
//! loudly as a config error instead of surfacing as an unexplained tolerance
//! problem twenty minutes later.
//!
//! ## Why TSV rather than the JSON twin
//!
//! Phase 3 approved exactly one new crate, `proptest`, and said nothing else.
//! Reading `parity.json` would need `serde_json`. Rather than spend the project's
//! one dependency request on a test reader — or hand-roll a JSON parser, which is
//! fragile and would rightly draw a reviewer's eye — the Python exporter emits a
//! tab-separated twin from the identical builder. `parity.json` remains the
//! canonical human-readable artifact; `test_parity_json_and_tsv_agree` on the
//! Python side asserts the two cannot drift.
//!
//! ## The tolerance, and the escape hatch
//!
//! Tolerance is read from the fixture (1e-9), not hardcoded here. It is not zero:
//! both implementations accumulate f64 through Welford and an EWMA, and identical
//! arithmetic in a different order differs in the last bits. 1e-9 is far tighter
//! than anything that could flip a flag decision at a threshold near 22.
//!
//! Per the pre-committed escape hatch: if parity could not be met at this
//! tolerance by end of day one of Phase 3, the phase ships with the achieved
//! tolerance documented and a BUILD_LOG entry naming the discrepancy. This test
//! prints the observed maximum delta on every run either way, so the number is
//! always in the output rather than in a claim.

use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

use spandan_core::ingest::Event;
use spandan_core::score::{Detector, DetectorConfig};

struct Fixture {
    tolerance: f64,
    config: DetectorConfig,
    events: Vec<Event>,
    expected: Vec<f64>,
}

fn fixture_path() -> PathBuf {
    // CARGO_MANIFEST_DIR is spandan-core/; the fixture lives at the repo root.
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("tests")
        .join("fixtures")
        .join("parity.tsv")
}

fn parse_config(cells: &[&str]) -> DetectorConfig {
    let mut kv: HashMap<&str, &str> = HashMap::new();
    for cell in cells {
        if let Some((key, value)) = cell.split_once('=') {
            kv.insert(key, value);
        }
    }
    let f = |key: &str| -> f64 {
        kv.get(key)
            .unwrap_or_else(|| panic!("fixture config is missing {key}"))
            .parse()
            .unwrap_or_else(|_| panic!("fixture config {key} is not a number"))
    };
    let i = |key: &str| -> i64 {
        kv.get(key)
            .unwrap_or_else(|| panic!("fixture config is missing {key}"))
            .parse()
            .unwrap_or_else(|_| panic!("fixture config {key} is not an integer"))
    };
    let b = |key: &str| -> bool {
        matches!(kv.get(key).copied(), Some("true") | Some("True"))
    };

    DetectorConfig {
        window_ms: i("window_ms"),
        ring_capacity: i("ring_capacity") as usize,
        baseline_sample_interval_ms: i("baseline_sample_interval_ms"),
        baseline_min_samples: i("baseline_min_samples") as u64,
        ewma_halflife_samples: f("ewma_halflife_samples"),
        w_velocity_bin: f("w_velocity_bin"),
        w_decline_bin: f("w_decline_bin"),
        w_amount: f("w_amount"),
        w_velocity_ip: f("w_velocity_ip"),
        w_repetition_damping: f("w_repetition_damping"),
        w_merchant_span_damping: f("w_merchant_span_damping"),
        threshold: f("threshold"),
        use_ewma: b("use_ewma"),
        use_per_ip: b("use_per_ip"),
    }
}

fn load_fixture() -> Fixture {
    let path = fixture_path();
    let text = fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "cannot read the committed parity fixture at {}: {e}.\n\
             It must be in git - `cargo test` has to work in a fresh clone with \
             no Python step. Regenerate with `python -m spandan.detect.parity`.",
            path.display()
        )
    });

    let mut tolerance = None;
    let mut config = None;
    let mut events = Vec::new();
    let mut expected = Vec::new();

    for line in text.lines() {
        if line.is_empty() {
            continue;
        }
        let cells: Vec<&str> = line.split('\t').collect();
        match cells[0] {
            "tolerance" => tolerance = Some(cells[1].parse().expect("tolerance is a float")),
            "config" => config = Some(parse_config(&cells[1..])),
            "columns" => {
                assert_eq!(
                    cells.last().copied(),
                    Some("expected_score"),
                    "the score must be the final column"
                );
                // Ground truth must never appear in the Rust core's contract.
                assert!(!cells.contains(&"label"), "fixture leaks `label`");
                assert!(!cells.contains(&"scenario_id"), "fixture leaks `scenario_id`");
            }
            _ => {
                let score: f64 = cells
                    .last()
                    .expect("row has cells")
                    .parse()
                    .expect("expected_score is a float");
                events.push(Event::from_fields(&cells).expect("fixture row parses"));
                expected.push(score);
            }
        }
    }

    Fixture {
        tolerance: tolerance.expect("fixture states a tolerance"),
        config: config.expect("fixture states a detector config"),
        events,
        expected,
    }
}

#[test]
fn parity_with_python_reference_fixture() {
    let fixture = load_fixture();
    assert_eq!(
        fixture.events.len(),
        fixture.expected.len(),
        "fixture has mismatched event and score counts"
    );
    assert!(
        fixture.events.len() > 1_000,
        "fixture is too small to be evidence of anything: {} events",
        fixture.events.len()
    );

    let mut detector = Detector::new(fixture.config.clone());
    let actual = detector.score_batch(&fixture.events);

    let mut max_delta = 0.0f64;
    let mut worst_index = 0usize;
    for (i, (got, want)) in actual.iter().zip(fixture.expected.iter()).enumerate() {
        let delta = (got - want).abs();
        if delta > max_delta {
            max_delta = delta;
            worst_index = i;
        }
    }

    println!("--- parity: rust core vs frozen python reference ---");
    println!("events compared      {}", fixture.events.len());
    println!("tolerance (absolute) {:e}", fixture.tolerance);
    println!("max abs score delta  {max_delta:e}");
    if max_delta > 0.0 {
        println!(
            "worst at index {worst_index}: rust {} vs python {}",
            actual[worst_index], fixture.expected[worst_index]
        );
    }
    let nonzero = fixture.expected.iter().filter(|s| **s != 0.0).count();
    println!("non-zero expected    {nonzero} of {}", fixture.expected.len());

    assert!(
        max_delta <= fixture.tolerance,
        "parity failed: max abs delta {max_delta:e} exceeds tolerance {:e}.\n\
         Worst at index {worst_index}: rust {} vs python {}.\n\
         The Python reference is the specification; this core is what is wrong.",
        fixture.tolerance,
        actual[worst_index],
        fixture.expected[worst_index]
    );
}

#[test]
fn parity_fixture_exercises_more_than_the_cold_start() {
    // A fixture where everything scores zero would pass parity trivially while
    // testing nothing but the warm-up guard.
    let fixture = load_fixture();
    let nonzero = fixture.expected.iter().filter(|s| **s != 0.0).count();
    assert!(
        nonzero > fixture.expected.len() / 4,
        "only {nonzero} of {} expected scores are non-zero; the fixture is not \
         exercising the scoring path",
        fixture.expected.len()
    );

    let peak = fixture.expected.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    assert!(
        peak > fixture.config.threshold,
        "no fixture event exceeds the threshold ({peak} vs {}); parity would not \
         cover the flagging path",
        fixture.config.threshold
    );
}

#[test]
fn streaming_and_batch_agree_on_the_fixture() {
    // The two surfaces must be the same state machine on real data, not just on
    // the synthetic sequences in the unit tests.
    let fixture = load_fixture();

    let batch = Detector::new(fixture.config.clone()).score_batch(&fixture.events);

    let mut detector = Detector::new(fixture.config.clone());
    let streamed: Vec<f64> = fixture.events.iter().map(|e| detector.update(e).0).collect();

    assert_eq!(batch, streamed, "score_batch and update disagree");
}
