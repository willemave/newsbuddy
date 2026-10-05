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

#[test]
fn decodes_padded_server_accepted_legacy_lens_cursor() {
    let cursor = "eyJsZW5zX2lkIjozLCJzZWdtZW50X2lkIjo5LCJjcmVhdGVkX2F0IjoiMjAyNi0wOC0zMFQxMjozNDo1Ni45OTk5OTkifQ==";
    let decoded = decode_lens_cursor(cursor).expect("legacy cursor decodes");
    assert_eq!(decoded.lens_id, 3);
    assert_eq!(decoded.segment_id, 9);
    assert_eq!(
        decoded.created_at,
        NaiveDateTime::parse_from_str("2026-08-30T12:34:56.999999", "%Y-%m-%dT%H:%M:%S%.6f")
            .expect("valid timestamp")
    );
}

#[tokio::test]
async fn invalid_lens_cursor_maps_to_recoverable_stale_cursor() {
    use axum::body::to_bytes;
    use axum::response::IntoResponse;

    let error =
        decode_lens_cursor_or_stale("not-a-cursor", "request-1").expect_err("invalid cursor fails");
    let response = error.into_response();
    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body = to_bytes(response.into_body(), 16 * 1024)
        .await
        .expect("body reads");
    let body: serde_json::Value = serde_json::from_slice(&body).expect("body is JSON");
    assert_eq!(body["code"], "stale_cursor");
}
