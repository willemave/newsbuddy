use super::*;

#[test]
fn runware_request_uses_provider_exact_task_uuid_field() {
    let task_uuid = "a770f077-f413-47de-9dac-be0b26a35da6";
    let request = RunwareRequest {
        task_type: "imageInference",
        task_uuid,
        include_cost: true,
        output_type: "URL",
        output_format: "PNG",
        positive_prompt: "A bounded fixture prompt",
        model: DEFAULT_RUNWARE_MODEL,
        number_results: 1,
        width: SEEDREAM_INFOGRAPHIC_WIDTH,
        height: SEEDREAM_INFOGRAPHIC_HEIGHT,
        negative_prompt: None,
    };

    let payload = serde_json::to_value([request]).expect("Runware request should serialize");
    let task = payload
        .as_array()
        .and_then(|tasks| tasks.first())
        .and_then(Value::as_object)
        .expect("Runware request should be a one-task array");

    assert_eq!(task.get("taskUUID"), Some(&Value::from(task_uuid)));
    assert!(!task.contains_key("taskUuid"));
    let parsed = Uuid::parse_str(task_uuid).expect("fixture task UUID should parse");
    assert_eq!(parsed.get_version_num(), 4);
}

#[test]
fn seedream_uses_provider_native_dimensions_without_negative_prompt() {
    let options = runware_request_options(DEFAULT_RUNWARE_MODEL);
    assert_eq!(options.width, 2_848);
    assert_eq!(options.height, 1_600);
    assert_eq!(options.negative_prompt, None);
}

#[test]
fn other_runware_models_use_standard_infographic_request() {
    let options = runware_request_options("runware:101@1");
    assert_eq!(options.width, 1_024);
    assert_eq!(options.height, 576);
    assert!(options.negative_prompt.is_some());
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
fn model_list_deduplicates_primary_and_fallback() {
    assert_eq!(
        resolve_models(" model-a ".to_owned(), Some("model-a".to_owned())).unwrap(),
        vec!["model-a"]
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

#[test]
fn runware_download_requires_public_https_url() {
    assert!(public_https_url("https://cdn.example.com/image.png").is_ok());
    assert!(public_https_url("http://cdn.example.com/image.png").is_err());
    assert!(public_https_url("https://127.0.0.1/image.png").is_err());
    assert!(public_https_url("https://user:secret@example.com/image.png").is_err());
}
