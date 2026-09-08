use super::*;
use chrono::{TimeZone as _, Utc};

fn row(fixture: &Value) -> ContentDetailProjection {
    let now = Utc.with_ymd_and_hms(2026, 9, 7, 12, 0, 0).unwrap();
    ContentDetailProjection {
        id: 42,
        content_type: fixture["content_type"].as_str().unwrap().to_owned(),
        url: "https://example.com/source".to_owned(),
        source_url: None,
        title: Some("Fixture title".to_owned()),
        source: Some("Example".to_owned()),
        platform: Some("web".to_owned()),
        status: "completed".to_owned(),
        error_message: None,
        retry_count: 0,
        content_metadata: fixture["metadata"].clone(),
        created_at: now,
        updated_at: Some(now),
        processed_at: Some(now),
        checked_out_by: None,
        checked_out_at: None,
        publication_date: Some(now),
        is_read: true,
        is_saved_to_knowledge: true,
        body_available: true,
        body_format: Some("markdown".to_owned()),
    }
}

const ARTIFACT_FIXTURES: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../../contracts/testing/content/artifact_responses.json"
));

#[test]
fn detail_and_list_baselines() {
    let legacy: Vec<Value> = serde_json::from_str(include_str!("baseline.json")).unwrap();
    let artifacts: Vec<Value> = serde_json::from_str(ARTIFACT_FIXTURES).unwrap();
    for fixture in legacy.iter().chain(&artifacts) {
        let detail = serde_json::to_value(present_content_detail(row(fixture)).unwrap()).unwrap();
        let list = serde_json::to_value(present_content_summary(row(fixture), None, None).unwrap())
            .unwrap();
        assert_eq!(list, fixture["list"], "list changed: {}", fixture["name"]);
        assert_eq!(
            detail, fixture["detail"],
            "detail changed: {}",
            fixture["name"]
        );
    }
    for fixture in artifacts {
        let _: newsly_providers::LongformArtifactEnvelope =
            serde_json::from_value(fixture["metadata"]["summary"].clone())
                .expect("shared artifact must satisfy the production envelope schema");
        assert_eq!(
            fixture["detail"]["longform_artifact"],
            fixture["metadata"]["summary"]
        );
        assert_eq!(
            fixture["detail"]["longform_artifact"],
            fixture["previous_detail"]["longform_artifact"]
        );
    }
}
