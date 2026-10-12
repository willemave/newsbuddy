use super::*;

#[test]
fn pdf_billing_preserves_296_observed_credits_and_page_counts() {
    let url = PublicUrl::parse("https://example.com/book.pdf").unwrap();
    let result = parse_response(
        StatusCode::OK,
        br#"{
        "success":true,"data":{"markdown":"Full document","metadata":{
            "creditsUsed":296,"numPages":296,"totalPages":296,"scrapeId":"provider-296"
        }}
    }"#,
        &url,
        0.0032,
    )
    .unwrap();
    assert_eq!(result.credits_used, Some(296));
    assert_eq!(result.num_pages, Some(296));
    assert_eq!(result.total_pages, Some(296));
    assert_eq!(result.scrape_id.as_deref(), Some("provider-296"));
    assert!((result.cost_usd.unwrap() - 296.0 * 0.0032).abs() < 1e-12);
}

#[test]
fn page_counts_do_not_invent_missing_credit_usage_or_cost() {
    let url = PublicUrl::parse("https://example.com/book.pdf").unwrap();
    for credits in [json!(null), json!(-1), json!("296"), json!(1.5)] {
        let body = serde_json::to_vec(&json!({
            "data":{"markdown":"Body", "metadata":{"creditsUsed":credits,"numPages":296}}
        }))
        .unwrap();
        let result = parse_response(StatusCode::OK, &body, &url, 0.0032).unwrap();
        assert_eq!(result.credits_used, None);
        assert_eq!(result.num_pages, Some(296));
        assert_eq!(result.cost_usd, None);
    }
    let result = parse_response(
        StatusCode::OK,
        br#"{"data":{"markdown":"Body","metadata":{"totalPages":296}}}"#,
        &url,
        0.0032,
    )
    .unwrap();
    assert_eq!(result.credits_used, None);
    assert_eq!(result.cost_usd, None);
}

#[test]
fn cached_zero_credit_response_remains_zero_and_failed_body_keeps_observed_billing() {
    let url = PublicUrl::parse("https://example.com/book.pdf").unwrap();
    let zero = parse_response(
        StatusCode::OK,
        br#"{"creditsUsed":0,"data":{"markdown":"Cached body"}}"#,
        &url,
        0.0032,
    )
    .unwrap();
    assert_eq!(zero.credits_used, Some(0));
    assert_eq!(zero.cost_usd, Some(0.0));
    let rejected = parse_response(
        StatusCode::OK,
        br#"{"success":false,"error":"Parsing failed","data":{"creditsUsed":296}}"#,
        &url,
        0.0032,
    )
    .unwrap();
    assert_eq!(rejected.credits_used, Some(296));
    assert!(rejected.markdown.is_none());
    assert_eq!(rejected.error.as_deref(), Some("Parsing failed"));
}

use serde_json::json;
