//! Public contract verification.

use serde_json::{Map, Value, json};

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
        ("delete", "/api/learning/decks/{deck_id}"),
        ("delete", "/api/scrapers/{config_id}"),
    ];
    const RETIRED_NO_SUCCESS_OPERATION_IDS: &[&str] = &[
        "retryContentChatSessionsCouncilModeBranch",
        "selectContentChatSessionsCouncilModeBranch",
        "startContentChatSessionsCouncilMode",
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

    fn retired_operation_without_success(operation: &Value) -> Option<&str> {
        let operation_id = operation.get("operationId")?.as_str()?;
        (RETIRED_NO_SUCCESS_OPERATION_IDS.contains(&operation_id)
            && operation.get("deprecated").and_then(Value::as_bool) == Some(true)
            && operation
                .get("responses")
                .and_then(Value::as_object)
                .is_some_and(|responses| responses.contains_key("410")))
        .then_some(operation_id)
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
        let mut seen_retired_operations = HashSet::new();

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
                if success_responses.is_empty() {
                    let operation_id = retired_operation_without_success(operation)
                        .unwrap_or_else(|| panic!("{method} {path} has no success response"));
                    seen_retired_operations.insert(operation_id);
                    continue;
                }
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
        assert_eq!(
            seen_retired_operations,
            RETIRED_NO_SUCCESS_OPERATION_IDS.iter().copied().collect()
        );
    }

    #[test]
    fn unrelated_operations_cannot_use_the_retired_no_success_exception() {
        let unrelated = json!({
            "operationId": "someOtherOperation",
            "deprecated": true,
            "responses": {"410": {"description": "Gone"}}
        });
        assert_eq!(retired_operation_without_success(&unrelated), None);

        let missing_gone = json!({
            "operationId": "startContentChatSessionsCouncilMode",
            "deprecated": true,
            "responses": {"401": {"description": "Unauthorized"}}
        });
        assert_eq!(retired_operation_without_success(&missing_gone), None);
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
