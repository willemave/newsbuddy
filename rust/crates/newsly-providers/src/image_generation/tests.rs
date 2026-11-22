use super::*;
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};

#[derive(Debug, Default)]
struct RecordingUsageObserver {
    observations: Mutex<Vec<ImageGenerationUsage>>,
}

impl ImageGenerationUsageObserver for RecordingUsageObserver {
    fn observe(
        &self,
        usage: ImageGenerationUsage,
    ) -> Pin<Box<dyn Future<Output = Result<(), String>> + Send + '_>> {
        Box::pin(async move {
            self.observations.lock().unwrap().push(usage);
            Ok(())
        })
    }
}

#[tokio::test]
async fn billable_response_is_observed_before_a_later_download_failure() {
    let observer = Arc::new(RecordingUsageObserver::default());
    observe_image_usage(
        Arc::clone(&observer) as Arc<dyn ImageGenerationUsageObserver>,
        ImageGenerationUsage {
            provider: "runware".to_owned(),
            model: DEFAULT_RUNWARE_MODEL.to_owned(),
            request_id: Some("task-response-1".to_owned()),
            input_tokens: None,
            cache_read_tokens: None,
            output_tokens: None,
            total_tokens: None,
            request_count: 1,
            response_cost_usd: Some(0.035),
            metadata: json!({"billable_image_count": 1}),
        },
    )
    .await
    .unwrap();

    let later_download: Result<Vec<u8>, ImageGenerationError> = Err(
        ImageGenerationError::RunwareImageStatus(StatusCode::BAD_GATEWAY),
    );
    assert!(later_download.is_err());
    let observations = observer.observations.lock().unwrap();
    assert_eq!(observations.len(), 1);
    assert_eq!(
        observations[0].request_id.as_deref(),
        Some("task-response-1")
    );
    assert_eq!(observations[0].response_cost_usd, Some(0.035));
}

#[test]
fn google_response_decodes_camel_case_inline_image() {
    let payload: GoogleGenerateResponse = serde_json::from_value(json!({
        "candidates": [{
            "content": {"parts": [{"inlineData": {
                "mimeType": "image/png",
                "data": "aW1hZ2U="
            }}]}
        }],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 20,
            "totalTokenCount": 30
        },
        "responseId": "response-1"
    }))
    .expect("Google response should decode");
    assert_eq!(payload.response_id.as_deref(), Some("response-1"));
    assert_eq!(payload.usage_metadata.unwrap().total, Some(30));
}

#[test]
fn runware_task_uuid_validation_error_is_retryable_and_fallback_allowed() {
    let error = runware_status_error(
        StatusCode::BAD_REQUEST,
        Some(&RunwareErrorPayload {
            message: Some("taskUUID already exists".to_owned()),
            code: None,
            parameter: Some("taskUUID".to_owned()),
        }),
    );
    assert!(error.retryable());
    assert!(error.fallback_allowed());
}

#[test]
fn runware_concurrency_limit_defers_to_queue_retry() {
    let error = runware_status_error(
        StatusCode::BAD_REQUEST,
        Some(&RunwareErrorPayload {
            message: Some("Too many concurrent requests".to_owned()),
            code: Some(json!("concurrentRequestLimitExceeded")),
            parameter: None,
        }),
    );
    assert!(error.runware_concurrency_limited());
    assert!(error.retryable());
    assert!(!error.fallback_allowed());
}

#[test]
fn model_list_deduplicates_primary_and_fallback() {
    assert_eq!(
        resolve_models(" model-a ".to_owned(), Some("model-a".to_owned())).unwrap(),
        vec!["model-a"]
    );
}

#[test]
fn podcast_prioritizes_muse_and_keeps_configured_runware_fallbacks() {
    let configured = [
        DEFAULT_RUNWARE_MODEL.to_owned(),
        PODCAST_RUNWARE_MODEL.to_owned(),
        "runware:101@1".to_owned(),
    ];
    assert_eq!(
        runware_models_for_subject(InfographicSubject::Podcast, &configured).collect::<Vec<_>>(),
        [
            PODCAST_RUNWARE_MODEL,
            DEFAULT_RUNWARE_MODEL,
            "runware:101@1"
        ]
    );
    assert_eq!(
        runware_models_for_subject(InfographicSubject::General, &configured).collect::<Vec<_>>(),
        [
            DEFAULT_RUNWARE_MODEL,
            PODCAST_RUNWARE_MODEL,
            "runware:101@1"
        ]
    );
}

#[test]
fn google_default_base_matches_auth_transport() {
    assert_eq!(default_google_api_url(None), DEFAULT_GOOGLE_API_URL);
    let global = GoogleImageAuth::Bearer {
        access_token: SecretString::from("secret".to_owned()),
        project: "project".to_owned(),
        location: "global".to_owned(),
    };
    assert_eq!(
        default_google_api_url(Some(&global)),
        DEFAULT_GOOGLE_VERTEX_API_URL
    );
    let regional = GoogleImageAuth::Bearer {
        access_token: SecretString::from("secret".to_owned()),
        project: "project".to_owned(),
        location: "us-central1".to_owned(),
    };
    assert_eq!(
        default_google_api_url(Some(&regional)),
        "https://us-central1-aiplatform.googleapis.com"
    );
}
