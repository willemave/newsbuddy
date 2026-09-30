use super::*;
use chrono::TimeZone;

#[test]
fn usage_text_renders_tokens_vendor_units_and_cost() {
    let generated_at = Utc.with_ymd_and_hms(2026, 8, 31, 12, 0, 0).unwrap();
    let summary = UsageSummary {
        generated_at,
        since: generated_at - Duration::hours(24),
        until: generated_at,
        group_by: "vendor".to_owned(),
        totals: UsageTotals {
            call_count: 2,
            input_tokens: 60,
            cache_read_tokens: 40,
            cache_write_tokens: 10,
            output_tokens: 40,
            total_tokens: 100,
            request_count: 2,
            resource_count: 9,
            cost_usd: Some(0.42),
            known_cost_usd: 0.42,
            public_list_estimate_usd: 0.0,
            provider_estimate_usd: 0.28,
            non_billable_record_count: 0,
            unpriced_call_count: 0,
            unpriced_reasons: BTreeMap::new(),
            resource_units: BTreeMap::from([
                ("search results".to_owned(), 8),
                ("transcription seconds".to_owned(), 1),
            ]),
            providers: BTreeMap::new(),
            models: BTreeMap::new(),
        },
        groups: vec![UsageGroup {
            key: "exa".to_owned(),
            call_count: 1,
            input_tokens: 0,
            cache_read_tokens: 0,
            cache_write_tokens: 0,
            output_tokens: 0,
            total_tokens: 0,
            request_count: 1,
            resource_count: 8,
            cost_usd: Some(0.28),
            known_cost_usd: 0.28,
            public_list_estimate_usd: 0.0,
            provider_estimate_usd: 0.28,
            non_billable_record_count: 0,
            unpriced_call_count: 0,
        }],
    };

    let rendered = summary.render_text();
    assert!(rendered.contains(
        "Totals: 2 calls, 100 tokens, 40 cache-read tokens, 10 cache-write tokens, \
         2 requests, 8 search results, 1 transcription seconds, $0.4200"
    ));
    assert!(rendered.contains("- exa: 1 calls, 1 requests, $0.2800"));
}

#[sqlx::test(migrations = false)]
async fn usage_cost_distinguishes_missing_partial_free_and_empty(pool: PgPool) {
    sqlx::query(
        "CREATE TABLE vendor_usage_records (
            provider text, model text, feature text, operation text, source text, user_id bigint,
            input_tokens bigint, cache_read_tokens bigint, cache_write_tokens bigint,
            output_tokens bigint, total_tokens bigint, request_count bigint, resource_count bigint,
            cost_usd double precision, cost_basis text, metadata json NOT NULL DEFAULT '{}',
            created_at timestamp NOT NULL
        )",
    )
    .execute(&pool)
    .await
    .unwrap();
    sqlx::query(
        "INSERT INTO vendor_usage_records
             (provider, resource_count, cost_usd, cost_basis, metadata, created_at) VALUES
         ('mixed', 2, 0.25, 'public_list_estimate', '{\"resource_count_unit\":\"pages\"}', timezone('UTC', now())),
         ('mixed', NULL, 0.05, 'provider_estimate', '{}', timezone('UTC', now())),
         ('mixed', NULL, NULL, NULL, '{\"cost_reason\":\"missing_rate\"}', timezone('UTC', now())),
         ('unknown', NULL, NULL, NULL, '{}', timezone('UTC', now())),
         ('free', 1, 0.0, 'non_billable', '{\"unit\":\"local requests\"}', timezone('UTC', now())),
         ('units', 1, 0.0, 'public_list_estimate', '{\"resource_count_unit\":\"image\"}', timezone('UTC', now())),
         ('units', 3, 0.0, 'public_list_estimate', '{\"resource_count_unit\":\"resource\"}', timezone('UTC', now())),
         ('units', 60, 0.0, 'public_list_estimate', '{\"resource_count_unit\":\"audio_second\"}', timezone('UTC', now()))",
    )
    .execute(&pool)
    .await
    .unwrap();
    let window = QueryWindow::ending_now(24).unwrap();
    let summary = load_usage_summary(&pool, window, UsageGroupBy::Provider)
        .await
        .unwrap();
    assert_eq!(summary.totals.cost_usd, None);
    assert!((summary.totals.known_cost_usd - 0.30).abs() < f64::EPSILON);
    assert!((summary.totals.public_list_estimate_usd - 0.25).abs() < f64::EPSILON);
    assert!((summary.totals.provider_estimate_usd - 0.05).abs() < f64::EPSILON);
    assert_eq!(summary.totals.non_billable_record_count, 1);
    assert_eq!(summary.totals.unpriced_call_count, 2);
    assert_eq!(summary.totals.unpriced_reasons["missing_rate"], 1);
    assert_eq!(summary.totals.unpriced_reasons["unknown"], 1);
    assert_eq!(summary.totals.resource_units["pages"], 2);
    assert_eq!(summary.totals.resource_units["local requests"], 1);
    assert_eq!(summary.totals.resource_units["image"], 1);
    assert_eq!(summary.totals.resource_units["resource"], 3);
    assert_eq!(summary.totals.resource_units["audio_second"], 60);
    let group = |key: &str| {
        summary
            .groups
            .iter()
            .find(|group| group.key == key)
            .unwrap()
    };
    assert_eq!(group("mixed").cost_usd, None);
    assert!((group("mixed").known_cost_usd - 0.30).abs() < f64::EPSILON);
    assert!((group("mixed").provider_estimate_usd - 0.05).abs() < f64::EPSILON);
    assert_eq!(group("unknown").cost_usd, None);
    assert_eq!(group("unknown").unpriced_call_count, 1);
    assert_eq!(group("free").cost_usd, Some(0.0));
    assert_eq!(group("free").unpriced_call_count, 0);
    assert!(
        summary
            .render_text()
            .contains("cost unknown ($0.3000 priced subtotal: $0.2500 public-list estimates, $0.0500 provider estimates; 1 non-billable, 2 unpriced records)")
    );
    assert!(
        summary
            .render_text()
            .contains("Unpriced reasons: missing_rate=1 unknown=1")
    );
    let json = serde_json::to_value(&summary).unwrap();
    assert!(json["totals"]["cost_usd"].is_null());
    assert_eq!(json["totals"]["known_cost_usd"], 0.30);
    sqlx::query("DELETE FROM vendor_usage_records")
        .execute(&pool)
        .await
        .unwrap();
    let empty = load_usage_summary(&pool, window, UsageGroupBy::Provider)
        .await
        .unwrap();
    assert_eq!(empty.totals.call_count, 0);
    assert_eq!(empty.totals.cost_usd, Some(0.0));
    assert_eq!(empty.totals.unpriced_call_count, 0);
    assert!(empty.groups.is_empty());
}
