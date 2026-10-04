use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use chrono::{DateTime, NaiveDateTime, SecondsFormat, Utc};
use serde::Serialize;
use serde::de::DeserializeOwned;
use thiserror::Error;

#[derive(Clone, Copy, Debug, Error, Eq, PartialEq)]
#[error("Invalid pagination cursor")]
pub(super) struct InvalidCursor;

pub(super) fn encode<T: Serialize>(payload: &T) -> Result<String, InvalidCursor> {
    serde_json::to_vec(payload)
        .map(|bytes| URL_SAFE_NO_PAD.encode(bytes))
        .map_err(|_| InvalidCursor)
}

pub(super) fn decode<T: DeserializeOwned>(value: &str) -> Result<T, InvalidCursor> {
    let bytes = URL_SAFE_NO_PAD.decode(value).map_err(|_| InvalidCursor)?;
    serde_json::from_slice(&bytes).map_err(|_| InvalidCursor)
}

pub(super) fn encode_timestamp(value: NaiveDateTime) -> String {
    DateTime::<Utc>::from_naive_utc_and_offset(value, Utc)
        .to_rfc3339_opts(SecondsFormat::Micros, true)
}

pub(super) fn decode_timestamp(value: &str) -> Result<NaiveDateTime, InvalidCursor> {
    DateTime::parse_from_rfc3339(value)
        .map(|value| value.naive_utc())
        .map_err(|_| InvalidCursor)
}

#[cfg(test)]
mod tests {
    use base64::Engine as _;
    use base64::engine::general_purpose::URL_SAFE_NO_PAD;
    use chrono::NaiveDateTime;
    use serde::{Deserialize, Serialize};

    use super::*;

    #[derive(Debug, Deserialize, PartialEq, Serialize)]
    #[serde(deny_unknown_fields)]
    struct Fixture {
        timestamp: String,
        rank: f64,
    }

    #[test]
    fn timestamps_preserve_postgres_microseconds() {
        for raw in ["2026-01-02T03:04:05.000000", "2026-01-02T03:04:05.999999"] {
            let timestamp = NaiveDateTime::parse_from_str(raw, "%Y-%m-%dT%H:%M:%S%.6f")
                .expect("valid timestamp");
            assert_eq!(
                decode_timestamp(&encode_timestamp(timestamp)),
                Ok(timestamp)
            );
        }
    }

    #[test]
    fn awkward_ranks_round_trip_bit_exactly() {
        for rank in [
            f64::from(0.1_f32),
            0.300_000_000_000_000_04,
            1.000_000_000_000_000_2,
        ] {
            let payload = Fixture {
                timestamp: encode_timestamp(
                    NaiveDateTime::parse_from_str(
                        "2026-01-02T03:04:05.123456",
                        "%Y-%m-%dT%H:%M:%S%.6f",
                    )
                    .expect("valid timestamp"),
                ),
                rank,
            };
            let decoded: Fixture =
                decode(&encode(&payload).expect("cursor encodes")).expect("cursor decodes");
            assert_eq!(decoded.rank.to_bits(), rank.to_bits());
        }
    }

    #[test]
    fn rejects_padding_unknown_fields_and_naive_timestamps() {
        let cursor = encode(&Fixture {
            timestamp: "2026-01-02T03:04:05.000000Z".to_owned(),
            rank: 1.0,
        })
        .expect("cursor encodes");
        assert!(decode::<Fixture>(&format!("{cursor}=")).is_err());

        let unknown = URL_SAFE_NO_PAD
            .encode(br#"{"timestamp":"2026-01-02T03:04:05.000000Z","rank":1.0,"extra":true}"#);
        assert!(decode::<Fixture>(&unknown).is_err());
        assert!(decode_timestamp("2026-01-02T03:04:05.000000").is_err());
    }
}
