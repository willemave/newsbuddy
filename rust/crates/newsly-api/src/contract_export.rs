//! Derived public contract documents exported from the Rust API definition.

use anyhow::Context;
use serde_json::{Map, Value, json};

const AGENT_OPERATIONS: &[(&str, &str, &str)] = &[
    ("/api/jobs/{job_id}", "get", "jobs"),
    ("/api/agent/search", "post", "search"),
    ("/api/agent/onboarding", "post", "onboarding"),
    ("/api/agent/onboarding/{run_id}", "get", "onboarding"),
    (
        "/api/agent/onboarding/{run_id}/complete",
        "post",
        "onboarding",
    ),
    ("/api/agent/cli/link/start", "post", "auth"),
    ("/api/agent/cli/link/{session_id}/approve", "post", "auth"),
    ("/api/agent/cli/link/{session_id}", "get", "auth"),
    ("/api/agent/library/manifest", "get", "library"),
    ("/api/agent/library/file", "get", "library"),
    ("/api/content/", "get", "content"),
    ("/api/content/{content_id}", "get", "content"),
    ("/api/content/submit", "post", "content"),
    ("/api/content/submissions/list", "get", "content"),
    ("/api/news/items", "get", "news"),
    ("/api/news/items/mark-read", "post", "news"),
    ("/api/news/items/{news_item_id}", "get", "news"),
    (
        "/api/news/items/{news_item_id}/convert-to-article",
        "post",
        "news",
    ),
    ("/api/scrapers/", "get", "sources"),
    ("/api/scrapers/subscribe", "post", "sources"),
];

/// Build the `OpenAPI` 3.0 document for Newsly's machine-oriented agent API surface.
///
/// The operation allowlist is intentionally compiled beside the Rust route document. Missing
/// operations fail export instead of silently shrinking the agent contract.
pub(crate) fn agent_openapi_document() -> anyhow::Result<Value> {
    let mut document = serde_json::to_value(newsly_api::openapi_document())
        .context("failed to serialize the Rust public OpenAPI document")?;
    let full_paths = document
        .get("paths")
        .and_then(Value::as_object)
        .context("Rust public OpenAPI document has no paths object")?;
    let mut filtered_paths = Map::new();

    for &(path, method, tag) in AGENT_OPERATIONS {
        let mut operation = full_paths
            .get(path)
            .and_then(Value::as_object)
            .and_then(|path_item| path_item.get(method))
            .cloned()
            .with_context(|| {
                format!(
                    "Rust public OpenAPI document is missing agent operation {} {}",
                    method.to_uppercase(),
                    path
                )
            })?;
        let operation_object = operation
            .as_object_mut()
            .with_context(|| format!("agent operation {method} {path} is not an object"))?;
        operation_object.insert("tags".to_owned(), json!([tag]));
        filtered_paths
            .entry(path.to_owned())
            .or_insert_with(|| Value::Object(Map::new()))
            .as_object_mut()
            .context("filtered agent path item is not an object")?
            .insert(method.to_owned(), operation);
    }

    let document_object = document
        .as_object_mut()
        .context("Rust public OpenAPI document is not an object")?;
    let version = document_object
        .get("info")
        .and_then(Value::as_object)
        .and_then(|info| info.get("version"))
        .and_then(Value::as_str)
        .unwrap_or("1.0.0")
        .to_owned();
    document_object.insert("openapi".to_owned(), Value::String("3.0.3".to_owned()));
    document_object.insert(
        "info".to_owned(),
        json!({
            "title": "Newsly Agent API",
            "version": version,
            "description": "Filtered machine-oriented API contract for Newsly agent clients."
        }),
    );
    document_object.insert("paths".to_owned(), Value::Object(filtered_paths));
    document_object.insert(
        "tags".to_owned(),
        json!([
            {"name": "jobs", "description": "Async job status routes."},
            {"name": "auth", "description": "CLI bootstrap and approval routes."},
            {"name": "search", "description": "Provider-backed discovery search."},
            {"name": "onboarding", "description": "Simplified onboarding routes."},
            {"name": "news", "description": "Visible short-form news item routes."},
            {"name": "content", "description": "Content listing, detail, and submission."},
            {"name": "sources", "description": "Runtime source subscription routes."},
            {"name": "library", "description": "Per-user markdown library sync routes."}
        ]),
    );

    normalize_openapi_30_shapes(&mut document)?;
    Ok(document)
}

fn normalize_openapi_30_shapes(value: &mut Value) -> anyhow::Result<()> {
    match value {
        Value::Array(items) => {
            for item in items {
                normalize_openapi_30_shapes(item)?;
            }
        }
        Value::Object(object) => {
            for item in object.values_mut() {
                normalize_openapi_30_shapes(item)?;
            }
            // JSON object keys are strings by definition; OpenAPI 3.0 has no `propertyNames`.
            object.remove("propertyNames");
            normalize_numeric_exclusivity(object, "exclusiveMinimum", "minimum");
            normalize_numeric_exclusivity(object, "exclusiveMaximum", "maximum");
            normalize_nullable_type_array(object);
            normalize_nullable_union(object)?;
        }
        _ => {}
    }
    Ok(())
}

fn normalize_numeric_exclusivity(
    object: &mut Map<String, Value>,
    exclusive_key: &str,
    bound_key: &str,
) {
    let Some(exclusive_value) = object.get(exclusive_key).cloned() else {
        return;
    };
    if !exclusive_value.is_number() {
        return;
    }
    object.insert(bound_key.to_owned(), exclusive_value);
    object.insert(exclusive_key.to_owned(), Value::Bool(true));
}

fn normalize_nullable_type_array(object: &mut Map<String, Value>) {
    let Some(Value::Array(types)) = object.get("type") else {
        return;
    };
    if types.len() != 2 {
        return;
    }
    let concrete_types = types
        .iter()
        .filter_map(Value::as_str)
        .filter(|value| *value != "null")
        .collect::<Vec<_>>();
    let has_null = types.iter().any(|value| value.as_str() == Some("null"));
    if !has_null || concrete_types.len() != 1 {
        return;
    }
    let concrete_type = concrete_types[0].to_owned();
    object.insert("type".to_owned(), Value::String(concrete_type));
    object.insert("nullable".to_owned(), Value::Bool(true));
}

fn normalize_nullable_union(object: &mut Map<String, Value>) -> anyhow::Result<()> {
    for union_key in ["anyOf", "oneOf"] {
        let Some(Value::Array(options)) = object.get(union_key) else {
            continue;
        };
        if options.len() != 2 {
            continue;
        }
        let null_count = options
            .iter()
            .filter(|option| {
                option
                    .as_object()
                    .and_then(|item| item.get("type"))
                    .and_then(Value::as_str)
                    == Some("null")
            })
            .count();
        if null_count != 1 {
            continue;
        }
        let concrete = options
            .iter()
            .find(|option| {
                option
                    .as_object()
                    .and_then(|item| item.get("type"))
                    .and_then(Value::as_str)
                    != Some("null")
            })
            .and_then(Value::as_object)
            .context("nullable OpenAPI union has no concrete object")?
            .clone();
        object.remove("anyOf");
        object.remove("oneOf");
        if concrete.contains_key("$ref") {
            object.insert(
                "allOf".to_owned(),
                Value::Array(vec![Value::Object(concrete)]),
            );
        } else {
            for (key, value) in concrete {
                object.entry(key).or_insert(value);
            }
        }
        object.insert("nullable".to_owned(), Value::Bool(true));
        return Ok(());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::collections::HashSet;

    use super::*;

    const HTTP_METHODS: &[&str] = &[
        "delete", "get", "head", "options", "patch", "post", "put", "trace",
    ];
    const CLIENT_DATETIME_FIELDS: &[(&str, &str)] = &[
        ("AgentLibraryDocumentResponse", "updated_at"),
        ("AgentLibraryFileResponse", "updated_at"),
        ("AgentLibraryManifestResponse", "generated_at"),
        ("AudioEpisodeResponse", "created_at"),
        ("AudioEpisodeResponse", "updated_at"),
        ("BriefingDiscussionDto", "updated_at"),
        ("BriefingIndexResponse", "generated_at"),
        ("BriefingSegmentDto", "created_at"),
        ("BriefingSourceDto", "published_at"),
        ("ChatMessageDto", "timestamp"),
        ("ChatSessionSummaryDto", "created_at"),
        ("ChatSessionSummaryDto", "last_message_at"),
        ("ChatSessionSummaryDto", "updated_at"),
        ("ChatToolProgressDto", "updated_at"),
        ("CliLinkApproveResponse", "expires_at"),
        ("CliLinkPollResponse", "expires_at"),
        ("CliLinkStartResponse", "expires_at"),
        ("ContentDetailResponse", "created_at"),
        ("ContentDetailResponse", "checked_out_at"),
        ("ContentDetailResponse", "publication_date"),
        ("ContentDetailResponse", "processed_at"),
        ("ContentDetailResponse", "updated_at"),
        ("ContentSummaryResponse", "created_at"),
        ("ContentSummaryResponse", "knowledge_saved_at"),
        ("ContentSummaryResponse", "publication_date"),
        ("ContentSummaryResponse", "processed_at"),
        ("NewsItemDetailResponse", "created_at"),
        ("NewsItemDetailResponse", "publication_date"),
        ("NewsItemDetailResponse", "processed_at"),
        ("NewsItemDetailResponse", "updated_at"),
        ("NewsItemSummaryResponse", "created_at"),
        ("NewsItemSummaryResponse", "publication_date"),
        ("NewsItemSummaryResponse", "processed_at"),
        ("JobStatusResponse", "completed_at"),
        ("JobStatusResponse", "created_at"),
        ("JobStatusResponse", "started_at"),
        ("LearningDeckResponse", "created_at"),
        ("LearningDeckResponse", "updated_at"),
        ("LearningDeckRunResponse", "completed_at"),
        ("LearningDeckRunResponse", "created_at"),
        ("LearningDeckRunResponse", "started_at"),
        ("LearningDeckRunResponse", "updated_at"),
        ("LearningDeckTimelineEntry", "created_at"),
        ("LearningDeckUrlResponse", "expires_at"),
        ("LlmTaskActionResponse", "approved_at"),
        ("LlmTaskActionResponse", "completed_at"),
        ("LlmTaskActionResponse", "created_at"),
        ("LlmTaskActionResponse", "started_at"),
        ("RecordContentInteractionRequest", "occurred_at"),
        ("ScraperConfigResponse", "created_at"),
        ("ScraperConfigStatsResponse", "latest_processed_at"),
        ("ScraperConfigStatsResponse", "last_fetch_at"),
        ("ScraperConfigStatsResponse", "latest_publication_at"),
        ("ScraperConfigStatsResponse", "next_expected_at"),
        ("ShareActionResponse", "created_at"),
        ("SubmissionStatusResponse", "created_at"),
        ("SubmissionStatusResponse", "processed_at"),
        ("UserLlmIntegrationResponse", "updated_at"),
        ("UserResponse", "created_at"),
        ("UserResponse", "updated_at"),
        ("XConnectionResponse", "last_synced_at"),
    ];
    const PLAIN_STRING_TIMESTAMP_FIELDS: &[(&str, &str)] = &[
        ("DiscussionCommentResponse", "created_at"),
        ("PodcastEpisodeSearchResultResponse", "published_at"),
    ];
    const NON_JSON_SUCCESS_OPERATIONS: &[(&str, &str)] = &[
        (
            "get",
            "/api/content/audio-episodes/{audio_episode_id}/audio",
        ),
        (
            "get",
            "/api/content/audio-episodes/{audio_episode_id}/stream",
        ),
        ("delete", "/api/content/chat/sessions/{session_id}"),
        ("delete", "/api/content/scrapers/{config_id}"),
        ("delete", "/api/learning/decks/{deck_id}"),
        ("delete", "/api/scrapers/{config_id}"),
    ];
    const NON_JSON_ERROR_RESPONSES: &[(&str, &str, &str)] = &[
        (
            "get",
            "/api/content/audio-episodes/{audio_episode_id}/audio",
            "416",
        ),
        (
            "get",
            "/api/content/audio-episodes/{audio_episode_id}/stream",
            "416",
        ),
    ];

    fn public_document() -> Value {
        serde_json::to_value(newsly_api::openapi_document()).expect("serialize OpenAPI")
    }

    #[test]
    fn rust_public_document_has_unique_operation_ids_and_no_retired_suggestions_route() {
        let document = public_document();
        let paths = document["paths"].as_object().expect("paths object");
        assert!(!paths.contains_key("/api/content/chat/sessions/{session_id}/initial-suggestions"));
        assert!(paths.contains_key("/health/live"));
        assert!(paths.contains_key("/health/ready"));

        let operation_ids = paths
            .values()
            .filter_map(Value::as_object)
            .flat_map(|path_item| {
                HTTP_METHODS
                    .iter()
                    .filter_map(|method| path_item.get(*method))
            })
            .filter_map(|operation| operation.get("operationId"))
            .filter_map(Value::as_str)
            .collect::<Vec<_>>();
        assert!(!operation_ids.is_empty());
        assert_eq!(
            operation_ids.len(),
            operation_ids.iter().copied().collect::<HashSet<_>>().len()
        );
    }

    #[test]
    fn rust_public_document_preserves_reviewed_operation_ids_and_responses() {
        let document = public_document();

        assert_eq!(
            document["paths"]["/api/agent/library/manifest"]["get"]["operationId"],
            "getAgentLibraryManifest"
        );
        assert_eq!(
            document["paths"]["/api/scrapers/"]["get"]["operationId"],
            "listScraperConfigs"
        );
        assert_eq!(
            document["paths"]["/api/content/scrapers/"]["get"]["operationId"],
            "listContentScraperConfigs"
        );
        assert_eq!(
            document["paths"]["/api/briefing"]["get"]["responses"]["304"],
            json!({"description": "Not Modified"})
        );
        assert_eq!(
            document["paths"]["/api/scrapers/subscribe"]["post"]["responses"]["200"]["content"]["application/json"]
                ["schema"],
            json!({"$ref": "#/components/schemas/ScraperConfigResponse"})
        );
        assert_eq!(
            document["paths"]["/api/news/items"]["get"]["responses"]["200"]["content"]["application/json"]
                ["schema"],
            json!({"$ref": "#/components/schemas/NewsItemListResponse"})
        );
        assert_eq!(
            document["paths"]["/api/news/items/{news_item_id}"]["get"]["responses"]["200"]["content"]
                ["application/json"]["schema"],
            json!({"$ref": "#/components/schemas/NewsItemDetailResponse"})
        );
        assert_eq!(
            document["components"]["schemas"]["SummaryVersion"],
            json!({"type": "integer", "enum": [1, 2]})
        );
        assert!(
            document["components"]["schemas"]["ContentDetailResponse"]["properties"]
                ["summary_version"]["oneOf"]
                .as_array()
                .is_some_and(|variants| variants.iter().any(|variant| {
                    variant["$ref"] == "#/components/schemas/SummaryVersion"
                }))
        );
    }

    #[test]
    fn rust_public_document_separates_response_presence_from_request_defaults() {
        let document = public_document();
        let schemas = document["components"]["schemas"]
            .as_object()
            .expect("component schemas object");

        for schema_name in [
            "ContentDetailResponse",
            "ContentSummaryResponse",
            "NewsItemDetailResponse",
            "NewsItemSummaryResponse",
            "PaginationMetadata",
            "UserResponse",
        ] {
            let schema = schemas[schema_name]
                .as_object()
                .unwrap_or_else(|| panic!("{schema_name} schema object"));
            let properties = schema["properties"]
                .as_object()
                .unwrap_or_else(|| panic!("{schema_name} properties object"));
            let required = schema["required"]
                .as_array()
                .unwrap_or_else(|| panic!("{schema_name} required array"))
                .iter()
                .filter_map(Value::as_str)
                .collect::<HashSet<_>>();
            assert_eq!(
                required.len(),
                properties.len(),
                "every serialized {schema_name} field must be guaranteed present"
            );
            assert!(
                properties
                    .keys()
                    .all(|field| required.contains(field.as_str())),
                "{schema_name} has a serialized field missing from required"
            );
        }

        let submit = schemas["SubmitContentRequest"]
            .as_object()
            .expect("SubmitContentRequest schema object");
        let required = submit["required"]
            .as_array()
            .expect("SubmitContentRequest required array")
            .iter()
            .filter_map(Value::as_str)
            .collect::<HashSet<_>>();
        assert!(!required.contains("crawl_links"));
        assert!(!required.contains("subscribe_to_feed"));
    }

    #[test]
    fn rust_public_document_types_every_api_success_response() {
        let document = public_document();
        let paths = document["paths"].as_object().expect("paths object");
        let mut seen_exceptions = HashSet::new();

        for (path, path_item) in paths {
            if !path.starts_with("/api/") {
                continue;
            }
            let path_item = path_item.as_object().expect("path item object");
            for method in HTTP_METHODS {
                let Some(operation) = path_item.get(*method) else {
                    continue;
                };
                let responses = operation["responses"]
                    .as_object()
                    .expect("responses object");
                let success_responses = responses
                    .iter()
                    .filter(|(status, _)| {
                        status
                            .parse::<u16>()
                            .is_ok_and(|status_code| (200..400).contains(&status_code))
                    })
                    .collect::<Vec<_>>();
                assert!(
                    !success_responses.is_empty(),
                    "{method} {path} has no success response"
                );
                let has_json_schema = success_responses.iter().any(|(_, response)| {
                    response
                        .pointer("/content/application~1json/schema")
                        .is_some()
                });
                if has_json_schema {
                    continue;
                }

                let operation_key = (*method, path.as_str());
                assert!(
                    NON_JSON_SUCCESS_OPERATIONS.contains(&operation_key),
                    "{method} {path} has no typed JSON success response"
                );
                seen_exceptions.insert(operation_key);

                if *method == "get" {
                    assert!(success_responses.iter().any(|(_, response)| {
                        response.pointer("/content/audio~1mpeg").is_some()
                    }));
                } else {
                    assert!(success_responses.iter().any(|(status, response)| {
                        *status == "204" && response.get("content").is_none()
                    }));
                }
            }
        }

        assert_eq!(
            seen_exceptions,
            NON_JSON_SUCCESS_OPERATIONS.iter().copied().collect()
        );
    }

    #[test]
    fn rust_public_document_preserves_client_datetime_semantics() {
        let document = public_document();

        for &(schema_name, property_name) in CLIENT_DATETIME_FIELDS {
            let property =
                &document["components"]["schemas"][schema_name]["properties"][property_name];
            assert_eq!(
                property["format"], "date-time",
                "{schema_name}.{property_name} must retain RFC 3339 date-time semantics"
            );
            let types = &property["type"];
            assert!(
                types == "string"
                    || types
                        .as_array()
                        .is_some_and(|values| values.iter().any(|value| value == "string")),
                "{schema_name}.{property_name} must remain a string wire value"
            );
        }

        for &(schema_name, property_name) in PLAIN_STRING_TIMESTAMP_FIELDS {
            let property =
                &document["components"]["schemas"][schema_name]["properties"][property_name];
            assert_eq!(
                property.get("format"),
                None,
                "{schema_name}.{property_name} is intentionally an opaque legacy string"
            );
        }
    }

    #[test]
    fn rust_public_document_preserves_typed_share_extension_boundaries() {
        let document = public_document();
        let schemas = document["components"]["schemas"]
            .as_object()
            .expect("component schemas");

        assert_eq!(
            schemas["LlmTaskMode"]["enum"],
            json!([
                "add_content",
                "add_to_briefing",
                "add_links",
                "add_feed",
                "chat",
                "presentation",
                "bookmark_only",
                "article_chat",
                "contextual_assistant",
                "learning_deck_presentation",
                "generic"
            ])
        );
        assert_schema_properties(
            schemas,
            "ShareActionCreateRequest",
            &[
                "approval_policy",
                "chat_initial_message",
                "instruction",
                "interests_prompt",
                "mode",
                "url",
            ],
        );
        assert_schema_properties(
            schemas,
            "ShareActionResponse",
            &[
                "actions",
                "created_at",
                "mode",
                "status",
                "task_id",
                "workflow_state",
            ],
        );
        assert_schema_properties(
            schemas,
            "RefreshTokenRequest",
            &["attempt_id", "refresh_token"],
        );
        assert_schema_properties(
            schemas,
            "AccessTokenResponse",
            &["access_token", "refresh_token", "token_type"],
        );
        assert_schema_properties(
            schemas,
            "ErrorEnvelope",
            &["code", "details", "message", "request_id", "retryable"],
        );
    }

    #[test]
    fn rust_public_document_uses_the_canonical_error_envelope() {
        let document = public_document();
        let paths = document["paths"].as_object().expect("paths object");
        let mut error_refs = HashSet::new();
        let mut seen_exceptions = HashSet::new();

        for (path, path_item) in paths {
            if !path.starts_with("/api/") && !path.starts_with("/auth/") {
                continue;
            }
            let path_item = path_item.as_object().expect("path item object");
            for method in HTTP_METHODS {
                let Some(operation) = path_item.get(*method) else {
                    continue;
                };
                let responses = operation["responses"]
                    .as_object()
                    .expect("responses object");
                for (status, response) in responses {
                    let is_error = status == "default"
                        || status
                            .parse::<u16>()
                            .is_ok_and(|status_code| status_code >= 400);
                    if !is_error {
                        continue;
                    }
                    let schema_ref = response
                        .pointer("/content/application~1json/schema/$ref")
                        .and_then(Value::as_str);
                    if let Some(schema_ref) = schema_ref {
                        error_refs.insert(schema_ref);
                        continue;
                    }

                    let response_key = (*method, path.as_str(), status.as_str());
                    assert!(
                        NON_JSON_ERROR_RESPONSES.contains(&response_key),
                        "{method} {path} response {status} has no JSON schema ref"
                    );
                    assert!(
                        response.get("content").is_none(),
                        "reviewed non-JSON error {method} {path} {status} must remain bodyless"
                    );
                    seen_exceptions.insert(response_key);
                }
            }
        }

        assert_eq!(
            error_refs,
            HashSet::from(["#/components/schemas/ErrorEnvelope"])
        );
        assert_eq!(
            seen_exceptions,
            NON_JSON_ERROR_RESPONSES.iter().copied().collect()
        );
    }

    #[test]
    fn agent_document_is_a_strict_openapi_30_projection() {
        let document = agent_openapi_document().expect("build agent OpenAPI");
        assert_eq!(document["openapi"], "3.0.3");
        assert_eq!(
            document["paths"].as_object().expect("agent paths").len(),
            AGENT_OPERATIONS.len()
        );
        assert_eq!(
            document["paths"]["/api/agent/cli/link/start"]["post"]["operationId"],
            "startCliLink"
        );
        assert_eq!(
            document["paths"]["/api/content/submissions/list"]["get"]["operationId"],
            "listContentSubmissionStatuses"
        );
        let tags = document["tags"]
            .as_array()
            .expect("agent tags")
            .iter()
            .filter_map(|tag| tag["name"].as_str())
            .collect::<HashSet<_>>();
        assert!(tags.is_superset(&HashSet::from(["auth", "content", "library"])));
        assert_no_openapi_31_nullable_shapes(&document);
    }

    fn assert_no_openapi_31_nullable_shapes(value: &Value) {
        match value {
            Value::Array(items) => {
                for item in items {
                    assert_no_openapi_31_nullable_shapes(item);
                }
            }
            Value::Object(object) => {
                assert!(
                    !object.contains_key("propertyNames"),
                    "OpenAPI 3.1 propertyNames leaked into agent schema: {object:?}"
                );
                assert!(
                    !(object.get("nullable") == Some(&Value::Bool(true))
                        && object.contains_key("$ref")),
                    "nullable OpenAPI reference must be wrapped with allOf: {object:?}"
                );
                if let Some(Value::Array(types)) = object.get("type") {
                    assert!(
                        !types.iter().any(|value| value.as_str() == Some("null")),
                        "OpenAPI 3.1 nullable type array leaked into agent schema: {object:?}"
                    );
                }
                for item in object.values() {
                    assert_no_openapi_31_nullable_shapes(item);
                }
            }
            _ => {}
        }
    }

    fn assert_schema_properties(
        schemas: &Map<String, Value>,
        schema_name: &str,
        expected: &[&str],
    ) {
        let actual = schemas[schema_name]["properties"]
            .as_object()
            .expect("schema properties")
            .keys()
            .map(String::as_str)
            .collect::<HashSet<_>>();
        assert_eq!(actual, expected.iter().copied().collect());
    }
}
