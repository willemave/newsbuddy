use super::*;

#[test]
fn lens_cursor_preserves_exact_microseconds() {
    let timestamp =
        NaiveDateTime::parse_from_str("2026-08-30T12:34:56.999999", "%Y-%m-%dT%H:%M:%S%.6f")
            .expect("valid timestamp");
    let cursor = encode_lens_cursor(3, 9, timestamp).expect("cursor encodes");
    let decoded = decode_lens_cursor(&cursor).expect("cursor decodes");
    assert_eq!(
        (decoded.lens_id, decoded.segment_id, decoded.created_at),
        (3, 9, timestamp)
    );
}
