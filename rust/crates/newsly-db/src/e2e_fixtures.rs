//! Deterministic, namespaced local fixtures for native iOS end-to-end tests.

use chrono::{DateTime, Duration, Utc};
use newsly_agent_runtime::{
    AssistantPart, MessagePart, MessageRole, NewslyMessage, NewslyTranscript, ProviderUsage,
    RequestPart, TranscriptFinishReason,
};
use serde::Serialize;
use serde_json::{Map, Value, json};
use sqlx::{PgPool, Postgres, Transaction};
use thiserror::Error;

mod metadata;

use metadata::{detail_metadata, fixture_url, knowledge_metadata};

const DETAIL_TITLE: &str = "A Practical Evaluation Loop for Small AI Teams";
const KNOWLEDGE_TITLE: &str = "Reliable Async Systems Field Notes";
const PROCESSING_TITLE: &str = "Deferred E2E Processing Fixture";
const CHAT_TITLE: &str = "How should small teams evaluate AI products?";
const CHAT_USER_MESSAGE: &str = "Summarize the reliability lessons.";
const CHAT_ASSISTANT_MESSAGE: &str = "Initial mocked assistant reply.";
const DECK_TITLE: &str = "A Practical Playbook for Reliable Async Systems";
const AUDIO_TITLE: &str = "Reliable async systems field notes, narrated";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IosE2eFixtureNamespace(String);

impl IosE2eFixtureNamespace {
    pub fn parse(value: &str) -> Result<Self, IosE2eFixtureError> {
        if value.trim() != value {
            return Err(IosE2eFixtureError::InvalidNamespace);
        }
        let valid_length = (1..=32).contains(&value.len());
        let mut characters = value.chars();
        let valid_first = characters
            .next()
            .is_some_and(|character| character.is_ascii_lowercase() || character.is_ascii_digit());
        let valid_rest = characters.all(|character| {
            character.is_ascii_lowercase() || character.is_ascii_digit() || character == '-'
        });
        if !valid_length || !valid_first || !valid_rest {
            return Err(IosE2eFixtureError::InvalidNamespace);
        }
        Ok(Self(value.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct E2eDatabaseIdentity {
    pub database_name: String,
    pub server_address: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct IosE2eContentFixtures {
    pub detail_content_id: i64,
    pub detail_title: String,
    pub knowledge_content_id: i64,
    pub knowledge_title: String,
    pub processing_content_id: i64,
    pub processing_title: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct IosE2eBriefingFixtures {
    pub lens_id: i64,
    pub lens_key: String,
    pub segment_id: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct IosE2eChatFixtures {
    pub session_id: i64,
    pub title: String,
    pub expected_user_message: String,
    pub expected_assistant_message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct IosE2eLearningFixtures {
    pub deck_id: i64,
    pub deck_title: String,
    pub viewer_available: bool,
    pub audio_episode_id: i64,
    pub audio_title: String,
    pub audio_file_available: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct IosE2eLocalArtifactPlan {
    pub deck_storage_prefix: String,
    pub deck_object_key: String,
    pub source_notes_object_key: String,
    pub source_notes_html_object_key: String,
    pub artifact_object_keys: Vec<String>,
    pub audio_storage_path: String,
    pub audio_content_type: String,
}

#[must_use]
pub fn ios_e2e_local_artifact_plan(namespace: &IosE2eFixtureNamespace) -> IosE2eLocalArtifactPlan {
    let namespace = namespace.as_str();
    let deck_storage_prefix = format!("content/e2e/{namespace}/learning-deck");
    let deck_object_key = format!("{deck_storage_prefix}/index.html");
    let source_notes_object_key = format!("{deck_storage_prefix}/source-notes.md");
    let source_notes_html_object_key = format!("{deck_storage_prefix}/source-notes.html");
    IosE2eLocalArtifactPlan {
        artifact_object_keys: vec![
            deck_object_key.clone(),
            source_notes_object_key.clone(),
            source_notes_html_object_key.clone(),
        ],
        deck_storage_prefix,
        deck_object_key,
        source_notes_object_key,
        source_notes_html_object_key,
        audio_storage_path: format!("e2e/{namespace}/narration.wav"),
        audio_content_type: "audio/wav".to_owned(),
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct IosE2eTaskFixtures {
    pub deferred_processing_task_id: i64,
    pub deferred_until: DateTime<Utc>,
    pub share_task_id: i64,
    pub share_action_id: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct IosE2eFixtureSeedReceipt {
    pub namespace: String,
    pub seeded_at: DateTime<Utc>,
    pub user_id: i64,
    pub user_email: String,
    pub content: IosE2eContentFixtures,
    pub briefing: IosE2eBriefingFixtures,
    pub chat: IosE2eChatFixtures,
    pub learning: IosE2eLearningFixtures,
    pub tasks: IosE2eTaskFixtures,
}

pub async fn inspect_e2e_database_identity(
    pool: &PgPool,
) -> Result<E2eDatabaseIdentity, IosE2eFixtureError> {
    let (database_name, server_address) = sqlx::query_as::<_, (String, Option<String>)>(
        "SELECT current_database(), host(inet_server_addr())",
    )
    .fetch_one(pool)
    .await?;
    Ok(E2eDatabaseIdentity {
        database_name,
        server_address,
    })
}

/// Replace one namespace's local iOS fixture graph in a single short transaction.
///
/// The only active queue row is deferred until the year 2999. It makes the Processing screen
/// observable without letting a worker claim it or invoke an external provider.
pub async fn seed_ios_e2e_fixture(
    pool: &PgPool,
    namespace: &IosE2eFixtureNamespace,
    artifacts: &IosE2eLocalArtifactPlan,
) -> Result<IosE2eFixtureSeedReceipt, IosE2eFixtureError> {
    let namespace = namespace.as_str();
    let seeded_at = Utc::now();
    let apple_id = format!("ios-e2e-{namespace}");
    let user_email = format!("ios-e2e+{namespace}@example.com");
    let mut transaction = pool.begin().await?;

    let user_id = upsert_fixture_user(&mut transaction, &apple_id, &user_email, namespace).await?;
    clear_user_fixture_graph(&mut transaction, user_id).await?;

    let detail_url = fixture_url(namespace, "detail");
    let knowledge_url = fixture_url(namespace, "knowledge");
    let processing_url = fixture_url(namespace, "processing");
    let detail_content_id = upsert_completed_content(
        &mut transaction,
        &detail_url,
        DETAIL_TITLE,
        detail_metadata(namespace),
        "Newsly E2E",
        seeded_at - Duration::minutes(35),
    )
    .await?;
    let knowledge_content_id = upsert_completed_content(
        &mut transaction,
        &knowledge_url,
        KNOWLEDGE_TITLE,
        knowledge_metadata(namespace),
        "Newsly Engineering",
        seeded_at - Duration::minutes(25),
    )
    .await?;
    let processing_content_id = upsert_processing_content(
        &mut transaction,
        &processing_url,
        PROCESSING_TITLE,
        namespace,
    )
    .await?;

    for content_id in [
        detail_content_id,
        knowledge_content_id,
        processing_content_id,
    ] {
        upsert_inbox_status(&mut transaction, user_id, content_id).await?;
    }
    sqlx::query(
        "DELETE FROM content_knowledge_saves WHERE user_id::bigint = $1 AND content_id::bigint = $2",
    )
    .bind(user_id)
    .bind(detail_content_id)
    .execute(&mut *transaction)
    .await?;
    sqlx::query(
        "DELETE FROM content_read_status WHERE user_id::bigint = $1 AND content_id::bigint = ANY($2::bigint[])",
    )
    .bind(user_id)
    .bind([detail_content_id, knowledge_content_id])
    .execute(&mut *transaction)
    .await?;
    save_to_knowledge(&mut transaction, user_id, knowledge_content_id).await?;

    let briefing = seed_briefing(
        &mut transaction,
        user_id,
        namespace,
        knowledge_content_id,
        seeded_at,
    )
    .await?;
    let chat = seed_chat(&mut transaction, user_id, knowledge_content_id, seeded_at).await?;
    let learning = seed_learning_surfaces(
        &mut transaction,
        user_id,
        namespace,
        knowledge_content_id,
        &knowledge_url,
        artifacts,
        seeded_at,
    )
    .await?;
    let tasks = seed_task_surfaces(
        &mut transaction,
        user_id,
        namespace,
        processing_content_id,
        knowledge_content_id,
        &knowledge_url,
        seeded_at,
    )
    .await?;

    transaction.commit().await?;
    Ok(IosE2eFixtureSeedReceipt {
        namespace: namespace.to_owned(),
        seeded_at,
        user_id,
        user_email,
        content: IosE2eContentFixtures {
            detail_content_id,
            detail_title: DETAIL_TITLE.to_owned(),
            knowledge_content_id,
            knowledge_title: KNOWLEDGE_TITLE.to_owned(),
            processing_content_id,
            processing_title: PROCESSING_TITLE.to_owned(),
        },
        briefing,
        chat,
        learning,
        tasks,
    })
}

async fn upsert_fixture_user(
    transaction: &mut Transaction<'_, Postgres>,
    apple_id: &str,
    email: &str,
    namespace: &str,
) -> Result<i64, sqlx::Error> {
    let council_personas = json!([
        {
            "id": "paul_graham",
            "display_name": "Paul Graham",
            "instruction_prompt": "",
            "sort_order": 0
        },
        {
            "id": "ben_thompson",
            "display_name": "Ben Thompson",
            "instruction_prompt": "",
            "sort_order": 1
        }
    ]);
    sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO users (
            apple_id, email, full_name, is_admin, is_active,
            has_completed_new_user_tutorial, has_completed_live_voice_onboarding,
            has_completed_onboarding, council_personas, reading_experience,
            created_at, updated_at
        )
        VALUES (
            $1, $2, $3, FALSE, TRUE,
            TRUE, TRUE, TRUE, $4, 'briefing',
            timezone('UTC', now()), timezone('UTC', now())
        )
        ON CONFLICT (apple_id) DO UPDATE SET
            email = EXCLUDED.email,
            full_name = EXCLUDED.full_name,
            is_active = TRUE,
            has_completed_new_user_tutorial = TRUE,
            has_completed_live_voice_onboarding = TRUE,
            has_completed_onboarding = TRUE,
            council_personas = EXCLUDED.council_personas,
            reading_experience = 'briefing',
            updated_at = timezone('UTC', now())
        RETURNING id::bigint
        "#,
    )
    .bind(apple_id)
    .bind(email)
    .bind(format!("iOS E2E {namespace}"))
    .bind(council_personas)
    .fetch_one(&mut **transaction)
    .await
}

async fn clear_user_fixture_graph(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<(), sqlx::Error> {
    for statement in [
        "DELETE FROM chat_messages WHERE session_id IN (SELECT id FROM chat_sessions WHERE user_id::bigint = $1)",
        "DELETE FROM chat_sessions WHERE user_id::bigint = $1",
        "DELETE FROM briefing_segments WHERE user_id::bigint = $1",
        "DELETE FROM briefing_lenses WHERE user_id::bigint = $1",
        "DELETE FROM learning_deck_runs WHERE user_id::bigint = $1",
        "DELETE FROM learning_decks WHERE user_id::bigint = $1",
        "DELETE FROM audio_episodes WHERE user_id::bigint = $1",
        "DELETE FROM llm_tasks WHERE user_id::bigint = $1 AND task_kind = 'share_action'",
        "DELETE FROM processing_tasks WHERE owner_user_id::bigint = $1 AND payload::jsonb ? 'fixture_namespace'",
    ] {
        sqlx::query(statement)
            .bind(user_id)
            .execute(&mut **transaction)
            .await?;
    }
    Ok(())
}

async fn upsert_completed_content(
    transaction: &mut Transaction<'_, Postgres>,
    url: &str,
    title: &str,
    metadata: Value,
    source: &str,
    publication_date: DateTime<Utc>,
) -> Result<i64, sqlx::Error> {
    sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO contents (
            content_type, url, source_url, title, source, status, retry_count,
            classification, content_metadata, created_at, updated_at, processed_at,
            publication_date, platform, is_aggregate, search_text
        )
        VALUES (
            'article', $1, $1, $2, $3, 'completed', 0,
            'to_read', $4, timezone('UTC', now()), timezone('UTC', now()),
            timezone('UTC', now()), $5, 'web', FALSE, $6
        )
        ON CONFLICT (url, content_type) DO UPDATE SET
            source_url = EXCLUDED.source_url,
            title = EXCLUDED.title,
            source = EXCLUDED.source,
            status = 'completed',
            error_message = NULL,
            retry_count = 0,
            classification = 'to_read',
            content_metadata = EXCLUDED.content_metadata,
            updated_at = timezone('UTC', now()),
            processed_at = timezone('UTC', now()),
            publication_date = EXCLUDED.publication_date,
            platform = 'web',
            is_aggregate = FALSE,
            search_text = EXCLUDED.search_text
        RETURNING id::bigint
        "#,
    )
    .bind(url)
    .bind(title)
    .bind(source)
    .bind(metadata)
    .bind(publication_date.naive_utc())
    .bind(format!("{title} async reliability evaluation systems"))
    .fetch_one(&mut **transaction)
    .await
}

async fn upsert_processing_content(
    transaction: &mut Transaction<'_, Postgres>,
    url: &str,
    title: &str,
    namespace: &str,
) -> Result<i64, sqlx::Error> {
    sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO contents (
            content_type, url, source_url, title, source, status, retry_count,
            classification, content_metadata, created_at, updated_at, platform,
            is_aggregate, search_text
        )
        VALUES (
            'article', $1, $1, $2, 'Newsly E2E', 'pending', 0,
            'to_read', $3, timezone('UTC', now()), timezone('UTC', now()), 'web',
            FALSE, $2
        )
        ON CONFLICT (url, content_type) DO UPDATE SET
            title = EXCLUDED.title,
            status = 'pending',
            error_message = NULL,
            retry_count = 0,
            content_metadata = EXCLUDED.content_metadata,
            updated_at = timezone('UTC', now()),
            processed_at = NULL,
            is_aggregate = FALSE,
            search_text = EXCLUDED.search_text
        RETURNING id::bigint
        "#,
    )
    .bind(url)
    .bind(title)
    .bind(json!({"fixture_namespace": namespace, "source": "ios_e2e"}))
    .fetch_one(&mut **transaction)
    .await
}

async fn upsert_inbox_status(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content_id: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        INSERT INTO content_status (user_id, content_id, status, created_at, updated_at)
        VALUES ($1::bigint::integer, $2::bigint::integer, 'inbox', timezone('UTC', now()), timezone('UTC', now()))
        ON CONFLICT (user_id, content_id) DO UPDATE SET
            status = 'inbox', updated_at = timezone('UTC', now())
        "#,
    )
    .bind(user_id)
    .bind(content_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn save_to_knowledge(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content_id: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        INSERT INTO content_knowledge_saves (user_id, content_id, saved_at, created_at)
        VALUES ($1::bigint::integer, $2::bigint::integer, timezone('UTC', now()), timezone('UTC', now()))
        ON CONFLICT (user_id, content_id) DO UPDATE SET saved_at = EXCLUDED.saved_at
        "#,
    )
    .bind(user_id)
    .bind(content_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn seed_briefing(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    namespace: &str,
    content_id: i64,
    seeded_at: DateTime<Utc>,
) -> Result<IosE2eBriefingFixtures, sqlx::Error> {
    sqlx::query(
        r#"
        INSERT INTO briefing_states (
            user_id, version, masthead_title, masthead_deck, last_append_at, last_sweep_at
        )
        VALUES ($1::bigint::integer, 1, 'Briefing', $2, timezone('UTC', now()), timezone('UTC', now()))
        ON CONFLICT (user_id) DO UPDATE SET
            version = 1,
            masthead_title = EXCLUDED.masthead_title,
            masthead_deck = EXCLUDED.masthead_deck,
            last_append_at = EXCLUDED.last_append_at,
            last_sweep_at = EXCLUDED.last_sweep_at
        "#,
    )
    .bind(user_id)
    .bind("A deterministic local edition for the Rust-owned iOS test boundary.")
    .execute(&mut **transaction)
    .await?;
    let lens_key = format!("e2e-{namespace}");
    let lens_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO briefing_lenses (
            user_id, key, tier, title, deck, position, status,
            centroid_weight, created_at, updated_at
        )
        VALUES (
            $1::bigint::integer, $2, 'longform', 'Async Reliability',
            'Transactions, contracts, durable queues, and the evidence behind them.',
            10, 'active', 0, timezone('UTC', now()), timezone('UTC', now())
        )
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .bind(&lens_key)
    .fetch_one(&mut **transaction)
    .await?;
    let source_key = format!("content:{content_id}");
    let blocks = json!([
        {
            "type": "passage",
            "weight": "lead",
            "paragraphs": [{
                "runs": [
                    {
                        "kind": "source_link",
                        "text": KNOWLEDGE_TITLE,
                        "source_key": source_key
                    },
                    {
                        "kind": "text",
                        "text": " connects transaction ownership, typed contracts, and deterministic test data into one reliability story."
                    }
                ]
            }]
        },
        {
            "type": "pullquote",
            "source_key": source_key,
            "text": "Release database connections before waiting on the network."
        },
        {
            "type": "passage",
            "paragraphs": [{
                "runs": [{
                    "kind": "text",
                    "text": "The practical result is a smaller runtime boundary with explicit ownership and reproducible local evidence."
                }]
            }]
        }
    ]);
    let segment_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO briefing_segments (
            lens_id, user_id, blocks, markdown_raw, narration_text, source_keys,
            status, model, prompt_version, warnings, created_at, updated_at, event_groups
        )
        VALUES (
            $1::bigint::integer, $2::bigint::integer, $3, $4, $5, $6,
            'active', 'deterministic-fixture', 'e2e-v1', '[]'::jsonb,
            $7, $7, '[]'::jsonb
        )
        RETURNING id::bigint
        "#,
    )
    .bind(lens_id)
    .bind(user_id)
    .bind(blocks)
    .bind(format!(
        "[{KNOWLEDGE_TITLE}](newsly://briefing/content/{content_id})"
    ))
    .bind("A short briefing about reliable asynchronous processing.")
    .bind(json!([source_key]))
    .bind((seeded_at - Duration::minutes(18)).naive_utc())
    .fetch_one(&mut **transaction)
    .await?;
    Ok(IosE2eBriefingFixtures {
        lens_id,
        lens_key,
        segment_id,
    })
}

async fn seed_chat(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content_id: i64,
    seeded_at: DateTime<Utc>,
) -> Result<IosE2eChatFixtures, IosE2eFixtureError> {
    let created_at = seeded_at - Duration::minutes(14);
    let session_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO chat_sessions (
            user_id, content_id, title, session_type, topic, llm_model, llm_provider,
            created_at, updated_at, last_message_at, is_archived, council_mode,
            is_hidden_from_history
        )
        VALUES (
            $1::bigint::integer, $2::bigint::integer, $3, 'knowledge_chat', 'Async reliability',
            'openai:gpt-5.6-terra', 'openai', $4, $4, $4, FALSE, FALSE, FALSE
        )
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .bind(content_id)
    .bind(CHAT_TITLE)
    .bind(created_at.naive_utc())
    .fetch_one(&mut **transaction)
    .await?;
    let transcript = completed_chat_transcript(created_at);
    let message_list = serde_json::to_string(&transcript)?;
    sqlx::query(
        r#"
        INSERT INTO chat_messages (
            session_id, message_list, created_at, status, render_metadata,
            stream_generation, stream_revision
        )
        VALUES ($1::bigint::integer, $2, $3, 'completed', '{}'::json, 0, 0)
        "#,
    )
    .bind(session_id)
    .bind(message_list)
    .bind(created_at.naive_utc())
    .execute(&mut **transaction)
    .await?;
    Ok(IosE2eChatFixtures {
        session_id,
        title: CHAT_TITLE.to_owned(),
        expected_user_message: CHAT_USER_MESSAGE.to_owned(),
        expected_assistant_message: CHAT_ASSISTANT_MESSAGE.to_owned(),
    })
}

async fn seed_learning_surfaces(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    namespace: &str,
    content_id: i64,
    content_url: &str,
    artifacts: &IosE2eLocalArtifactPlan,
    seeded_at: DateTime<Utc>,
) -> Result<IosE2eLearningFixtures, sqlx::Error> {
    let deck_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO learning_decks (
            user_id, source_kind, source_identity, source_url, source_content_id,
            source_title, source_metadata, title, artifact_storage_prefix,
            deck_object_key, source_notes_object_key, source_notes_html_object_key,
            artifact_object_keys, share_enabled, created_at, updated_at
        )
        VALUES (
            $1::bigint::integer, 'content', $2, $3, $4::bigint::integer,
            $5, $6, $7, $8, $9, $10, $11, $12, FALSE, $13, $13
        )
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .bind(format!("ios-e2e:{namespace}:content:{content_id}"))
    .bind(content_url)
    .bind(content_id)
    .bind(KNOWLEDGE_TITLE)
    .bind(json!({"content_type": "article", "fixture_namespace": namespace}))
    .bind(DECK_TITLE)
    .bind(&artifacts.deck_storage_prefix)
    .bind(&artifacts.deck_object_key)
    .bind(&artifacts.source_notes_object_key)
    .bind(&artifacts.source_notes_html_object_key)
    .bind(json!(&artifacts.artifact_object_keys))
    .bind((seeded_at - Duration::minutes(12)).naive_utc())
    .fetch_one(&mut **transaction)
    .await?;
    let timeline_time = (seeded_at - Duration::minutes(10)).to_rfc3339();
    let run_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO learning_deck_runs (
            deck_id, user_id, status, source_snapshot, timeline,
            artifact_object_keys, completed_at, created_at, updated_at
        )
        VALUES (
            $1::bigint::integer, $2::bigint::integer, 'completed', $3, $4,
            $5, $6, $6, $6
        )
        RETURNING id::bigint
        "#,
    )
    .bind(deck_id)
    .bind(user_id)
    .bind(json!({
        "source_kind": "content",
        "source_identity": format!("content:{content_id}"),
        "source_url": content_url,
        "source_title": KNOWLEDGE_TITLE
    }))
    .bind(json!([{
        "status": "completed",
        "note": "Deterministic fixture published",
        "created_at": timeline_time
    }]))
    .bind(json!(&artifacts.artifact_object_keys))
    .bind((seeded_at - Duration::minutes(10)).naive_utc())
    .fetch_one(&mut **transaction)
    .await?;
    sqlx::query(
        r#"
        UPDATE learning_decks
        SET latest_run_id = $2::bigint::integer,
            latest_successful_run_id = $2::bigint::integer,
            updated_at = $3
        WHERE id::bigint = $1
        "#,
    )
    .bind(deck_id)
    .bind(run_id)
    .bind((seeded_at - Duration::minutes(10)).naive_utc())
    .execute(&mut **transaction)
    .await?;

    let audio_episode_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO audio_episodes (
            user_id, kind, status, title, source_content_id, input_hash,
            source_item_ids, source_snapshot, script, script_text, prompt_version,
            model, audio_storage_path, audio_content_type, duration_seconds, completed_at,
            created_at, updated_at, share_enabled
        )
        VALUES (
            $1::bigint::integer, 'custom_narration', 'completed', $2,
            $3::bigint::integer, $4, '[]'::jsonb, $5, $6, $7, 1,
            'deterministic-fixture', $8, $9, 1, $10, $10, $10, FALSE
        )
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .bind(AUDIO_TITLE)
    .bind(content_id)
    .bind(format!("ios-e2e-{namespace}-narration"))
    .bind(json!({
        "kind": "custom_narration",
        "fixture_namespace": namespace,
        "content_ids": [content_id],
        "news_item_ids": [],
        "source_count": 1,
        "items": [{"content_id": content_id, "title": KNOWLEDGE_TITLE}]
    }))
    .bind(json!({
        "title": AUDIO_TITLE,
        "segments": [{"speaker": "host", "text": "A deterministic narration fixture."}]
    }))
    .bind("A deterministic narration fixture for the async reliability field notes.")
    .bind(&artifacts.audio_storage_path)
    .bind(&artifacts.audio_content_type)
    .bind((seeded_at - Duration::minutes(8)).naive_utc())
    .fetch_one(&mut **transaction)
    .await?;

    Ok(IosE2eLearningFixtures {
        deck_id,
        deck_title: DECK_TITLE.to_owned(),
        viewer_available: true,
        audio_episode_id,
        audio_title: AUDIO_TITLE.to_owned(),
        audio_file_available: true,
    })
}

async fn seed_task_surfaces(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    namespace: &str,
    processing_content_id: i64,
    knowledge_content_id: i64,
    knowledge_url: &str,
    seeded_at: DateTime<Utc>,
) -> Result<IosE2eTaskFixtures, IosE2eFixtureError> {
    let executor_version = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT active_version
        FROM runtime_ownership
        WHERE resource_kind = 'task_type'
          AND resource_key = 'process_content'
          AND active_owner = 'rust'
          AND transition_state = 'active'
        "#,
    )
    .fetch_optional(&mut **transaction)
    .await?
    .ok_or(IosE2eFixtureError::MissingProcessContentOwnership)?;
    let deferred_until = DateTime::parse_from_rfc3339("2999-01-01T00:00:00Z")
        .expect("fixed deferred fixture time is valid")
        .with_timezone(&Utc);
    let processing_task_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO processing_tasks (
            task_type, content_id, payload, status, created_at, retry_count,
            queue_name, available_at, owner_user_id, executor_runtime,
            executor_version, executor_namespace
        )
        VALUES (
            'process_content', $1::bigint::integer, $2, 'pending',
            timezone('UTC', now()), 0, 'content', $3, $4::bigint::integer,
            'rust', $5, 'process_content'
        )
        RETURNING id::bigint
        "#,
    )
    .bind(processing_content_id)
    .bind(json!({
        "content_id": processing_content_id,
        "fixture_namespace": namespace,
        "deferred_fixture": true
    }))
    .bind(deferred_until.naive_utc())
    .bind(user_id)
    .bind(executor_version)
    .fetch_one(&mut **transaction)
    .await?;
    sqlx::query(
        r#"
        INSERT INTO processing_task_user_access (task_id, user_id, created_at)
        VALUES ($1::bigint::integer, $2::bigint::integer, timezone('UTC', now()))
        ON CONFLICT DO NOTHING
        "#,
    )
    .bind(processing_task_id)
    .bind(user_id)
    .execute(&mut **transaction)
    .await?;

    let task_created_at = seeded_at - Duration::minutes(6);
    let task_completed_at = seeded_at - Duration::minutes(5);
    let share_task_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO llm_tasks (
            user_id, task_kind, mode, workflow_key, workflow_version,
            workflow_state, status, approval_policy, allowed_actions, tool_policy,
            vm_namespace, prompt_pack, input_json, output_json, artifact_manifest,
            usage_json, status_history, created_at, updated_at, started_at,
            completed_at, subject_id
        )
        VALUES (
            $1::bigint::integer, 'share_action', 'bookmark_only',
            'share_action.bookmark_only.v1', 1, 'completed', 'completed',
            '{"default":"auto_apply"}'::jsonb, '["save_to_knowledge"]'::jsonb,
            '{}'::jsonb, $2, 'share_action.bookmark_only', $3, $4,
            '{}'::jsonb, '{}'::jsonb, $5, $6, $7, $6, $7,
            $8::bigint::integer
        )
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .bind(format!("user:{user_id}"))
    .bind(json!({
        "url": knowledge_url,
        "mode": "bookmark_only",
        "instruction": Value::Null,
        "chat_initial_message": Value::Null,
        "interests_prompt": Value::Null,
        "fixture_namespace": namespace
    }))
    .bind(json!({"action": "save_to_knowledge", "content_id": knowledge_content_id}))
    .bind(json!([
        {
            "status": "queued",
            "workflow_state": "queued",
            "note": "Fixture task created",
            "created_at": task_created_at
        },
        {
            "status": "completed",
            "workflow_state": "completed",
            "note": "Fixture action applied",
            "created_at": task_completed_at
        }
    ]))
    .bind(task_created_at.naive_utc())
    .bind(task_completed_at.naive_utc())
    .bind(knowledge_content_id)
    .fetch_one(&mut **transaction)
    .await?;
    let share_action_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO llm_task_actions (
            llm_task_id, action_name, action_status, approval_policy,
            approval_required, action_input, action_result, rationale,
            idempotency_key, created_at, updated_at, started_at, completed_at
        )
        VALUES (
            $1::bigint::integer, 'save_to_knowledge', 'applied', 'auto_apply',
            FALSE, $2, $3, 'Saved by the deterministic local Share fixture.',
            $4, $5, $5, $5, $5
        )
        RETURNING id::bigint
        "#,
    )
    .bind(share_task_id)
    .bind(json!({"content_id": knowledge_content_id, "url": knowledge_url}))
    .bind(json!({
        "content_id": knowledge_content_id,
        "created": false,
        "fixture_namespace": namespace
    }))
    .bind(format!("ios-e2e:{namespace}:save-to-knowledge"))
    .bind(task_completed_at.naive_utc())
    .fetch_one(&mut **transaction)
    .await?;

    Ok(IosE2eTaskFixtures {
        deferred_processing_task_id: processing_task_id,
        deferred_until,
        share_task_id,
        share_action_id,
    })
}

fn completed_chat_transcript(created_at: DateTime<Utc>) -> NewslyTranscript {
    NewslyTranscript {
        stream_generation: 0,
        messages: vec![
            NewslyMessage {
                id: None,
                role: MessageRole::User,
                parts: vec![MessagePart::Request(RequestPart::Text {
                    text: CHAT_USER_MESSAGE.to_owned(),
                })],
                created_at,
                run_id: None,
                provider: None,
                model: None,
                finish_reason: None,
                usage: ProviderUsage::default(),
                metadata: Map::new(),
            },
            NewslyMessage {
                id: None,
                role: MessageRole::Assistant,
                parts: vec![MessagePart::Assistant(AssistantPart::Text {
                    text: CHAT_ASSISTANT_MESSAGE.to_owned(),
                })],
                created_at,
                run_id: Some("ios-e2e-fixture".to_owned()),
                provider: Some("fixture".to_owned()),
                model: Some("deterministic".to_owned()),
                finish_reason: Some(TranscriptFinishReason::Stop),
                usage: ProviderUsage::default(),
                metadata: Map::new(),
            },
        ],
        ..NewslyTranscript::default()
    }
}

#[derive(Debug, Error)]
pub enum IosE2eFixtureError {
    #[error(
        "fixture namespace must be 1-32 lowercase ASCII letters, digits, or hyphens and start with a letter or digit"
    )]
    InvalidNamespace,
    #[error("Rust process_content ownership is not active in this database")]
    MissingProcessContentOwnership,
    #[error("iOS E2E fixture database operation failed: {0}")]
    Sqlx(#[from] sqlx::Error),
    #[error("iOS E2E chat transcript serialization failed: {0}")]
    TranscriptSerialization(#[from] serde_json::Error),
}

#[cfg(test)]
mod tests;
