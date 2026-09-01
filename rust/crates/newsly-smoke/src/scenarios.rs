use std::future::Future;
use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail, ensure};
use chrono::Utc;
use newsly_contracts::{
    AssistantTurnResponse, ChatMessageRole, ChatSessionDetailDto, CreateChatSessionResponse,
    ErrorEnvelope, LearningDeckResponse, LearningDeckShareResponse, LearningDeckUrlResponse,
    LlmTaskMode, LlmTaskStatus, MessageProcessingStatus, MessageStatusResponse,
    SendMessageResponse, ShareActionResponse, TokenResponse,
};
use reqwest::StatusCode;
use serde_json::{Map, Value, json};
use tokio::time::sleep;
use url::Url;

use crate::api::SmokeApi;
use crate::report::{ScenarioReport, SmokeReport};

#[derive(Debug, Clone)]
pub(crate) struct SmokeConfig {
    pub base_url: Url,
    pub source_url: Url,
    pub run_id: String,
    pub scenario_timeout: Duration,
    pub poll_interval: Duration,
}

#[derive(Debug, Clone)]
struct AuthContext {
    primary: SmokeApi,
    secondary: SmokeApi,
    primary_user_id: i64,
    secondary_user_id: i64,
}

#[derive(Debug, Clone, Copy)]
struct DeckContext {
    deck_id: i64,
    content_id: i64,
}

#[derive(Debug, Clone, Copy)]
struct DeckArtifactEvidence {
    viewer_bytes: usize,
    source_notes_bytes: usize,
    revoked_status: StatusCode,
}

pub(crate) async fn run(config: SmokeConfig) -> SmokeReport {
    let started_at = Utc::now();
    let unauthenticated = SmokeApi::new(config.base_url.clone(), Duration::from_secs(90))
        .expect("validated smoke HTTP client configuration");
    let mut scenarios = Vec::new();

    let auth = record(&mut scenarios, "stack_and_authentication", || {
        stack_and_authentication(&unauthenticated)
    })
    .await;
    let Some(auth) = auth else {
        return finish_report(config, started_at, scenarios);
    };

    record(&mut scenarios, "direct_chat", || {
        direct_chat(&config, &auth)
    })
    .await;
    record(&mut scenarios, "share_extension_to_chat", || {
        share_extension_to_chat(&config, &auth)
    })
    .await;
    let deck = record(&mut scenarios, "share_extension_to_learning_deck", || {
        share_extension_to_learning_deck(&config, &auth)
    })
    .await;
    if let Some(deck) = deck {
        record(&mut scenarios, "learning_deck_grounded_chat", || {
            learning_deck_grounded_chat(&config, &auth, deck)
        })
        .await;
    }
    record(&mut scenarios, "failure_and_ownership_boundaries", || {
        failure_and_ownership_boundaries(&auth)
    })
    .await;

    finish_report(config, started_at, scenarios)
}

async fn record<T, F, Fut>(
    reports: &mut Vec<ScenarioReport>,
    name: &'static str,
    run: F,
) -> Option<T>
where
    F: FnOnce() -> Fut,
    Fut: Future<Output = Result<(T, Map<String, Value>)>>,
{
    let started = Instant::now();
    match run().await {
        Ok((value, evidence)) => {
            reports.push(ScenarioReport::passed(
                name,
                started.elapsed().as_millis(),
                evidence,
            ));
            Some(value)
        }
        Err(error) => {
            reports.push(ScenarioReport::failed(
                name,
                started.elapsed().as_millis(),
                &error,
            ));
            None
        }
    }
}

fn finish_report(
    config: SmokeConfig,
    started_at: chrono::DateTime<Utc>,
    scenarios: Vec<ScenarioReport>,
) -> SmokeReport {
    SmokeReport {
        schema_version: 1,
        run_id: config.run_id,
        base_url: config.base_url.to_string(),
        source_url: config.source_url.to_string(),
        started_at,
        completed_at: Utc::now(),
        scenarios,
    }
}

async fn stack_and_authentication(api: &SmokeApi) -> Result<(AuthContext, Map<String, Value>)> {
    let _: Value = api.get("/health/live").await?;
    let _: Value = api.get("/health/ready").await?;
    let unauthenticated_status = api.get_status("/api/learning/decks").await?;
    ensure!(
        matches!(
            unauthenticated_status,
            StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN
        ),
        "protected API returned {unauthenticated_status} without authentication"
    );
    let request = json!({
        "has_completed_onboarding": true,
        "has_completed_new_user_tutorial": true
    });
    let primary: TokenResponse = api.post("/auth/debug/new-user", &request).await?;
    let secondary: TokenResponse = api.post("/auth/debug/new-user", &request).await?;
    ensure!(primary.user.id != secondary.user.id, "debug users collided");
    let context = AuthContext {
        primary: api.authenticated(primary.access_token),
        secondary: api.authenticated(secondary.access_token),
        primary_user_id: primary.user.id,
        secondary_user_id: secondary.user.id,
    };
    Ok((
        context,
        evidence([
            ("primary_user_id", json!(primary.user.id)),
            ("secondary_user_id", json!(secondary.user.id)),
            (
                "unauthenticated_status",
                json!(unauthenticated_status.as_u16()),
            ),
        ]),
    ))
}

async fn direct_chat(config: &SmokeConfig, auth: &AuthContext) -> Result<((), Map<String, Value>)> {
    let created: CreateChatSessionResponse = auth
        .primary
        .post(
            "/api/content/chat/sessions",
            &json!({"topic": format!("Local staging smoke {}", config.run_id)}),
        )
        .await?;
    let session_id = created.session.id;
    let first = send_and_wait(
        config,
        &auth.primary,
        session_id,
        "Reply with one short sentence confirming this live chat turn works.",
    )
    .await?;
    let second = send_and_wait(
        config,
        &auth.primary,
        session_id,
        "In one short sentence, say that this is the second durable turn.",
    )
    .await?;
    let detail: ChatSessionDetailDto = auth
        .primary
        .get(&format!("/api/content/chat/sessions/{session_id}"))
        .await?;
    let user_count = detail
        .messages
        .iter()
        .filter(|message| message.role == ChatMessageRole::User)
        .count();
    let assistant_count = detail
        .messages
        .iter()
        .filter(|message| message.role == ChatMessageRole::Assistant)
        .count();
    ensure!(user_count >= 2, "direct transcript omitted a user turn");
    ensure!(
        assistant_count >= 2,
        "direct transcript omitted an assistant turn"
    );
    ensure!(
        detail.messages.iter().all(|message| !matches!(
            message.role,
            ChatMessageRole::System | ChatMessageRole::Tool
        )),
        "visible transcript exposed system or tool chatter"
    );
    let foreign_status = auth
        .secondary
        .get_status(&format!("/api/content/chat/sessions/{session_id}"))
        .await?;
    ensure!(
        matches!(
            foreign_status,
            StatusCode::NOT_FOUND | StatusCode::FORBIDDEN
        ),
        "cross-user chat read returned {foreign_status}"
    );
    Ok((
        (),
        evidence([
            ("session_id", json!(session_id)),
            ("first_message_id", json!(first.message_id)),
            ("second_message_id", json!(second.message_id)),
            ("user_message_count", json!(user_count)),
            ("assistant_message_count", json!(assistant_count)),
        ]),
    ))
}

async fn share_extension_to_chat(
    config: &SmokeConfig,
    auth: &AuthContext,
) -> Result<((), Map<String, Value>)> {
    let created = create_share_action(
        &auth.primary,
        &config.source_url,
        LlmTaskMode::Chat,
        Some(format!("What is the main idea? [{}]", config.run_id)),
        None,
    )
    .await?;
    let completed = wait_for_share_action(config, &auth.primary, created.task_id).await?;
    require_hidden(
        auth.secondary
            .get_status(&format!("/api/share-actions/{}", created.task_id))
            .await?,
        "cross-user Share Action",
    )?;
    let action = completed
        .actions
        .iter()
        .find(|action| action.action_name == "enqueue_chat")
        .context("completed chat Share Action omitted enqueue_chat result")?;
    let content_id = positive_result_id(&action.action_result, "content_id")?;
    let session_id = positive_result_id(&action.action_result, "chat_session_id")?;
    let detail = wait_for_session_assistant(config, &auth.primary, session_id).await?;
    ensure!(
        detail.session.content_id == Some(content_id),
        "Share chat session was not grounded in its content"
    );

    let duplicate: newsly_contracts::ContentSubmissionResponse = auth
        .primary
        .post(
            "/api/content/submit",
            &json!({
                "url": config.source_url.to_string(),
                "save_to_knowledge_and_mark_read": true
            }),
        )
        .await?;
    ensure!(duplicate.already_exists, "duplicate source was not reused");
    ensure!(
        duplicate.content_id == content_id,
        "duplicate source changed identity"
    );
    Ok((
        (),
        evidence([
            ("share_task_id", json!(completed.task_id)),
            ("content_id", json!(content_id)),
            ("chat_session_id", json!(session_id)),
            ("transcript_message_count", json!(detail.messages.len())),
            ("duplicate_reused", json!(duplicate.already_exists)),
        ]),
    ))
}

async fn share_extension_to_learning_deck(
    config: &SmokeConfig,
    auth: &AuthContext,
) -> Result<(DeckContext, Map<String, Value>)> {
    let interests_prompt = format!(
        "Build a concise teaching deck for a technical reader. Smoke run {}.",
        config.run_id
    );
    let created = create_share_action(
        &auth.primary,
        &config.source_url,
        LlmTaskMode::Presentation,
        None,
        Some(interests_prompt.clone()),
    )
    .await?;
    let completed = wait_for_share_action(config, &auth.primary, created.task_id).await?;
    require_hidden(
        auth.secondary
            .get_status(&format!("/api/share-actions/{}", created.task_id))
            .await?,
        "cross-user Share Action",
    )?;
    let action = completed
        .actions
        .iter()
        .find(|action| action.action_name == "create_learning_deck")
        .context("completed presentation Share Action omitted deck result")?;
    let deck_id = positive_result_id(&action.action_result, "learning_deck_id")?;
    let content_id = positive_result_id(&action.action_result, "content_id")?;

    let active_conflict = auth
        .primary
        .post_expect_status(
            "/api/learning/decks",
            &json!({
                "content_id": content_id,
                "interests_prompt": interests_prompt
            }),
            StatusCode::CONFLICT,
        )
        .await?;
    let active_conflict: ErrorEnvelope = serde_json::from_value(active_conflict)
        .context("active Learning Deck conflict did not return ErrorEnvelope")?;
    ensure!(
        active_conflict.code == "invalid_state",
        "active Learning Deck conflict returned code {}",
        active_conflict.code
    );

    let deck = wait_for_deck(config, &auth.primary, deck_id).await?;
    require_hidden(
        auth.secondary
            .get_status(&format!("/api/learning/decks/{deck_id}"))
            .await?,
        "cross-user Learning Deck",
    )?;
    ensure!(deck.viewer_available, "completed deck has no viewer");
    ensure!(
        deck.source_notes_available,
        "completed deck has no source notes"
    );
    ensure!(
        deck.latest_successful_run_id.is_some(),
        "completed deck has no successful run"
    );

    let artifacts = validate_deck_artifacts_and_sharing(&auth.primary, deck_id).await?;

    Ok((
        DeckContext {
            deck_id,
            content_id,
        },
        evidence([
            ("share_task_id", json!(completed.task_id)),
            ("learning_deck_id", json!(deck_id)),
            ("content_id", json!(content_id)),
            ("active_conflict_code", json!(active_conflict.code)),
            (
                "latest_successful_run_id",
                json!(deck.latest_successful_run_id),
            ),
            ("viewer_bytes", json!(artifacts.viewer_bytes)),
            ("source_notes_bytes", json!(artifacts.source_notes_bytes)),
            (
                "sharing_revoked_status",
                json!(artifacts.revoked_status.as_u16()),
            ),
        ]),
    ))
}

async fn validate_deck_artifacts_and_sharing(
    api: &SmokeApi,
    deck_id: i64,
) -> Result<DeckArtifactEvidence> {
    let viewer: LearningDeckUrlResponse = api
        .post(
            &format!("/api/learning/decks/{deck_id}/viewer-url"),
            &json!({}),
        )
        .await?;
    let notes: LearningDeckUrlResponse = api
        .post(
            &format!("/api/learning/decks/{deck_id}/source-notes-url"),
            &json!({}),
        )
        .await?;
    let viewer_bytes = require_nonempty_page(api, &viewer.url, "signed viewer").await?;
    let source_notes_bytes = require_nonempty_page(api, &notes.url, "signed source notes").await?;

    let sharing: LearningDeckShareResponse = api
        .post(&format!("/api/learning/decks/{deck_id}/share"), &json!({}))
        .await?;
    ensure!(sharing.share_enabled, "public deck sharing did not enable");
    let share_url = sharing.share_url.context("enabled share omitted URL")?;
    require_nonempty_page(api, &share_url, "public viewer").await?;
    let disabled: LearningDeckShareResponse = api
        .delete(&format!("/api/learning/decks/{deck_id}/share"))
        .await?;
    ensure!(
        !disabled.share_enabled,
        "public deck sharing did not disable"
    );
    let revoked_status = api.get_text(&share_url).await?.0;
    ensure!(
        matches!(revoked_status, StatusCode::NOT_FOUND | StatusCode::GONE),
        "revoked public viewer returned {revoked_status}"
    );
    Ok(DeckArtifactEvidence {
        viewer_bytes,
        source_notes_bytes,
        revoked_status,
    })
}

async fn learning_deck_grounded_chat(
    config: &SmokeConfig,
    auth: &AuthContext,
    deck: DeckContext,
) -> Result<((), Map<String, Value>)> {
    let first = assistant_turn(
        &auth.primary,
        None,
        deck,
        format!(
            "Using only this deck and source, give one important takeaway. [{}]",
            config.run_id
        ),
    )
    .await?;
    let first_status = wait_for_message(config, &auth.primary, first.message_id).await?;
    let second = assistant_turn(
        &auth.primary,
        Some(first.session.id),
        deck,
        "Give one different takeaway without searching the web.".to_owned(),
    )
    .await?;
    let second_status = wait_for_message(config, &auth.primary, second.message_id).await?;
    let detail: ChatSessionDetailDto = auth
        .primary
        .get(&format!("/api/content/chat/sessions/{}", first.session.id))
        .await?;
    ensure!(
        detail.messages.iter().all(|message| !matches!(
            message.role,
            ChatMessageRole::System | ChatMessageRole::Tool
        )),
        "deck transcript exposed tool or system chatter"
    );
    Ok((
        (),
        evidence([
            ("learning_deck_id", json!(deck.deck_id)),
            ("chat_session_id", json!(first.session.id)),
            ("first_message_id", json!(first_status.message_id)),
            ("second_message_id", json!(second_status.message_id)),
            ("transcript_message_count", json!(detail.messages.len())),
        ]),
    ))
}

async fn failure_and_ownership_boundaries(auth: &AuthContext) -> Result<((), Map<String, Value>)> {
    let invalid_share = auth
        .primary
        .post_expect_status(
            "/api/share-actions",
            &json!({"url": "not-a-url", "mode": "chat"}),
            StatusCode::UNPROCESSABLE_ENTITY,
        )
        .await
        .context("invalid Share Action did not return typed 422")?;
    let invalid_deck = auth
        .primary
        .post_expect_status(
            "/api/learning/decks",
            &json!({}),
            StatusCode::UNPROCESSABLE_ENTITY,
        )
        .await
        .context("invalid Learning Deck did not return typed 422")?;
    let invalid_share_error: ErrorEnvelope = serde_json::from_value(invalid_share)
        .context("invalid Share Action did not return ErrorEnvelope")?;
    let invalid_deck_error: ErrorEnvelope = serde_json::from_value(invalid_deck)
        .context("invalid Learning Deck did not return ErrorEnvelope")?;
    ensure!(!invalid_share_error.request_id.trim().is_empty());
    ensure!(!invalid_deck_error.request_id.trim().is_empty());
    let unknown_deck = auth
        .secondary
        .get_status("/api/learning/decks/9223372036854770000")
        .await?;
    require_hidden(unknown_deck, "unknown Learning Deck")?;
    let unknown_chat = auth
        .secondary
        .get_status("/api/content/chat/sessions/9223372036854770000")
        .await?;
    require_hidden(unknown_chat, "unknown chat session")?;
    let unknown_share = auth
        .secondary
        .get_status("/api/share-actions/9223372036854770000")
        .await?;
    require_hidden(unknown_share, "unknown Share Action")?;
    ensure!(auth.primary_user_id != auth.secondary_user_id);
    Ok((
        (),
        evidence([
            ("invalid_share_code", json!(invalid_share_error.code)),
            ("invalid_deck_code", json!(invalid_deck_error.code)),
            ("unknown_deck_status", json!(unknown_deck.as_u16())),
            ("unknown_chat_status", json!(unknown_chat.as_u16())),
            ("unknown_share_status", json!(unknown_share.as_u16())),
        ]),
    ))
}

fn require_hidden(status: StatusCode, label: &str) -> Result<()> {
    ensure!(
        matches!(status, StatusCode::NOT_FOUND | StatusCode::FORBIDDEN),
        "{label} returned {status}"
    );
    Ok(())
}

async fn create_share_action(
    api: &SmokeApi,
    source_url: &Url,
    mode: LlmTaskMode,
    chat_initial_message: Option<String>,
    interests_prompt: Option<String>,
) -> Result<ShareActionResponse> {
    api.post(
        "/api/share-actions",
        &json!({
            "url": source_url.to_string(),
            "mode": mode.as_str(),
            "chat_initial_message": chat_initial_message,
            "interests_prompt": interests_prompt
        }),
    )
    .await
}

async fn wait_for_share_action(
    config: &SmokeConfig,
    api: &SmokeApi,
    task_id: i64,
) -> Result<ShareActionResponse> {
    poll(config, "Share Action", || async {
        let task: ShareActionResponse = api.get(&format!("/api/share-actions/{task_id}")).await?;
        match task.status {
            LlmTaskStatus::Completed => Ok(Some(task)),
            LlmTaskStatus::Failed | LlmTaskStatus::Cancelled => {
                bail!(
                    "Share Action {task_id} ended as {:?}: {task:?}",
                    task.status
                )
            }
            _ => Ok(None),
        }
    })
    .await
}

async fn wait_for_deck(
    config: &SmokeConfig,
    api: &SmokeApi,
    deck_id: i64,
) -> Result<LearningDeckResponse> {
    poll(config, "Learning Deck", || async {
        let deck: LearningDeckResponse = api.get(&format!("/api/learning/decks/{deck_id}")).await?;
        if deck.viewer_available && deck.latest_successful_run_id.is_some() {
            return Ok(Some(deck));
        }
        let terminal_failure = deck.latest_run.as_ref().is_some_and(|run| {
            matches!(
                run.status,
                newsly_contracts::LearningDeckRunStatus::Failed
                    | newsly_contracts::LearningDeckRunStatus::Cancelled
            )
        });
        if terminal_failure {
            bail!("Learning Deck {deck_id} failed: {deck:?}");
        }
        Ok(None)
    })
    .await
}

async fn send_and_wait(
    config: &SmokeConfig,
    api: &SmokeApi,
    session_id: i64,
    message: &str,
) -> Result<MessageStatusResponse> {
    let sent: SendMessageResponse = api
        .post(
            &format!("/api/content/chat/sessions/{session_id}/messages"),
            &json!({"message": message}),
        )
        .await?;
    wait_for_message(config, api, sent.message_id).await
}

async fn wait_for_message(
    config: &SmokeConfig,
    api: &SmokeApi,
    message_id: i64,
) -> Result<MessageStatusResponse> {
    poll(config, "chat message", || async {
        let status: MessageStatusResponse = api
            .get(&format!("/api/content/chat/messages/{message_id}/status"))
            .await?;
        match status.status {
            MessageProcessingStatus::Completed => {
                ensure!(
                    status
                        .assistant_message
                        .as_ref()
                        .is_some_and(|message| !message.content.trim().is_empty()),
                    "completed message {message_id} has no assistant content"
                );
                Ok(Some(status))
            }
            MessageProcessingStatus::Failed => {
                bail!("chat message {message_id} failed: {:?}", status.error)
            }
            MessageProcessingStatus::Processing => Ok(None),
        }
    })
    .await
}

async fn wait_for_session_assistant(
    config: &SmokeConfig,
    api: &SmokeApi,
    session_id: i64,
) -> Result<ChatSessionDetailDto> {
    poll(config, "Share chat transcript", || async {
        let detail: ChatSessionDetailDto = api
            .get(&format!("/api/content/chat/sessions/{session_id}"))
            .await?;
        if let Some(failed) = detail
            .messages
            .iter()
            .find(|message| message.status == MessageProcessingStatus::Failed)
        {
            bail!(
                "Share chat message {} failed: {:?}",
                failed.id,
                failed.error
            );
        }
        let complete_assistant = detail.messages.iter().any(|message| {
            message.role == ChatMessageRole::Assistant
                && message.status == MessageProcessingStatus::Completed
                && !message.content.trim().is_empty()
        });
        Ok(complete_assistant.then_some(detail))
    })
    .await
}

async fn assistant_turn(
    api: &SmokeApi,
    session_id: Option<i64>,
    deck: DeckContext,
    message: String,
) -> Result<AssistantTurnResponse> {
    api.post(
        "/api/content/chat/assistant/turns",
        &json!({
            "message": message,
            "session_id": session_id,
            "screen_context": {
                "screen_type": "learning_deck",
                "screen_title": format!("Smoke Learning Deck {}", deck.deck_id),
                "content_id": deck.content_id,
                "selected_topic": "Learning Deck",
                "note": format!(
                    "Deck ID: {}\nSource content ID: {}\nCurrent slide: 1",
                    deck.deck_id, deck.content_id
                )
            }
        }),
    )
    .await
}

async fn require_nonempty_page(api: &SmokeApi, url: &str, label: &str) -> Result<usize> {
    let (status, body) = api.get_text(url).await?;
    ensure!(status.is_success(), "{label} returned {status}");
    ensure!(
        body.len() >= 64,
        "{label} returned only {} bytes",
        body.len()
    );
    Ok(body.len())
}

async fn poll<T, F, Fut>(config: &SmokeConfig, label: &str, mut check: F) -> Result<T>
where
    F: FnMut() -> Fut,
    Fut: Future<Output = Result<Option<T>>>,
{
    let deadline = Instant::now() + config.scenario_timeout;
    loop {
        if let Some(value) = check().await? {
            return Ok(value);
        }
        if Instant::now() >= deadline {
            bail!(
                "{label} did not complete within {:?}",
                config.scenario_timeout
            );
        }
        sleep(config.poll_interval).await;
    }
}

fn positive_result_id(result: &Map<String, Value>, field: &str) -> Result<i64> {
    result
        .get(field)
        .and_then(Value::as_i64)
        .filter(|value| *value > 0)
        .with_context(|| format!("action result omitted positive {field}: {result:?}"))
}

fn evidence<const N: usize>(values: [(&str, Value); N]) -> Map<String, Value> {
    values
        .into_iter()
        .map(|(key, value)| (key.to_owned(), value))
        .collect()
}

#[cfg(test)]
mod tests {
    use serde_json::{Map, json};

    use super::positive_result_id;

    #[test]
    fn action_identity_extraction_requires_positive_integer() {
        let good = Map::from_iter([("deck_id".to_owned(), json!(42))]);
        assert_eq!(positive_result_id(&good, "deck_id").unwrap(), 42);
        for value in [json!(0), json!(-1), json!("42"), json!(null)] {
            let bad = Map::from_iter([("deck_id".to_owned(), value)]);
            assert!(positive_result_id(&bad, "deck_id").is_err());
        }
    }
}
