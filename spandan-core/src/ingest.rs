//! Stage 1 of 5 — the typed event.
//!
//! The boundary where untyped input becomes something the rest of the core can
//! rely on. Every field the detector is allowed to see is here, and nothing
//! else: there is no `label` and no `scenario_id` on this struct, so no amount
//! of later carelessness can leak ground truth into a score.

use std::fmt;

/// Authorization outcome as the acquirer reported it.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Status {
    Approved,
    Declined,
}

impl Status {
    pub fn parse(text: &str) -> Result<Self, IngestError> {
        match text {
            "approved" => Ok(Status::Approved),
            "declined" => Ok(Status::Declined),
            other => Err(IngestError::BadStatus(other.to_string())),
        }
    }

    #[inline]
    pub fn is_declined(self) -> bool {
        matches!(self, Status::Declined)
    }
}

/// One transaction attempt.
///
/// Owned `String`s rather than borrowed slices: the detector retains events in
/// per-entity ring buffers for the length of a window, so borrowing from a
/// caller's buffer would tie the core's lifetime to the caller's. The cost is
/// one allocation per identifier, which Phase 4 measures rather than assumes.
#[derive(Clone, Debug)]
pub struct Event {
    /// Epoch milliseconds, UTC.
    pub ts: i64,
    pub txn_id: String,
    pub merchant_id: String,
    pub bin: String,
    pub card_ref: String,
    pub ip: String,
    pub device_id: String,
    pub amount_paise: i64,
    pub status: Status,
}

#[derive(Debug)]
pub enum IngestError {
    BadStatus(String),
    BadField {
        field: &'static str,
        value: String,
    },
    WrongFieldCount {
        expected: usize,
        got: usize,
    },
}

impl fmt::Display for IngestError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            IngestError::BadStatus(v) => write!(f, "unknown status {v:?}"),
            IngestError::BadField { field, value } => {
                write!(f, "field {field} could not parse from {value:?}")
            }
            IngestError::WrongFieldCount { expected, got } => {
                write!(f, "expected {expected} fields, got {got}")
            }
        }
    }
}

impl std::error::Error for IngestError {}

/// Column order, matching `spandan.gen.schema.FEATURE_COLUMNS` exactly.
pub const FEATURE_COLUMNS: [&str; 9] = [
    "ts",
    "txn_id",
    "merchant_id",
    "bin",
    "card_ref",
    "ip",
    "device_id",
    "amount_paise",
    "status",
];

impl Event {
    /// Build from tab-separated fields in `FEATURE_COLUMNS` order.
    ///
    /// Used by the parity test to replay the committed fixture. Kept here rather
    /// than in the test so the column contract lives next to the struct it
    /// describes — if the two drift, this is the one place to look.
    pub fn from_fields(fields: &[&str]) -> Result<Self, IngestError> {
        if fields.len() < FEATURE_COLUMNS.len() {
            return Err(IngestError::WrongFieldCount {
                expected: FEATURE_COLUMNS.len(),
                got: fields.len(),
            });
        }
        let parse_i64 = |value: &str, field: &'static str| -> Result<i64, IngestError> {
            value.parse::<i64>().map_err(|_| IngestError::BadField {
                field,
                value: value.to_string(),
            })
        };
        Ok(Event {
            ts: parse_i64(fields[0], "ts")?,
            txn_id: fields[1].to_string(),
            merchant_id: fields[2].to_string(),
            bin: fields[3].to_string(),
            card_ref: fields[4].to_string(),
            ip: fields[5].to_string(),
            device_id: fields[6].to_string(),
            amount_paise: parse_i64(fields[7], "amount_paise")?,
            status: Status::parse(fields[8])?,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_well_formed_row() {
        let fields = vec![
            "1780252321599",
            "txn_000000000",
            "mer_000",
            "000022",
            "card_0000000000",
            "198.19.48.54",
            "dev_0000000000",
            "84903",
            "approved",
        ];
        let event = Event::from_fields(&fields).expect("should parse");
        assert_eq!(event.ts, 1_780_252_321_599);
        assert_eq!(event.amount_paise, 84_903);
        assert_eq!(event.status, Status::Approved);
        assert!(!event.status.is_declined());
    }

    #[test]
    fn rejects_an_unknown_status_rather_than_defaulting() {
        // Defaulting to approved would silently change the decline ratio, which
        // is the detector's primary signal.
        let fields = vec![
            "1", "t", "m", "b", "c", "i", "d", "100", "maybe",
        ];
        assert!(matches!(
            Event::from_fields(&fields),
            Err(IngestError::BadStatus(_))
        ));
    }

    #[test]
    fn rejects_a_short_row() {
        let fields = vec!["1", "t", "m"];
        assert!(matches!(
            Event::from_fields(&fields),
            Err(IngestError::WrongFieldCount { .. })
        ));
    }

    #[test]
    fn feature_columns_carry_no_ground_truth() {
        // The Python side asserts the same thing about its own schema. Both
        // halves of the contract are checked on their own side of the boundary.
        assert!(!FEATURE_COLUMNS.contains(&"label"));
        assert!(!FEATURE_COLUMNS.contains(&"scenario_id"));
        assert_eq!(FEATURE_COLUMNS.len(), 9);
    }
}
