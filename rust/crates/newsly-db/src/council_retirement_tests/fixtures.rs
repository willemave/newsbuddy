use chrono::{DateTime, Duration, Utc};
use newsly_agent_runtime::{
    AssistantPart, MessagePart, MessageRole, NewslyMessage, NewslyTranscript, ProviderUsage,
    RequestPart,
};
use serde_json::{Map, Value, json};
use sqlx::migrate::Migrator;
use sqlx::{Executor, PgPool, Postgres};

use crate::migrations::MIGRATOR;

pub(super) const RETIREMENT_VERSION: i64 = 20_261_002_010_000;
pub(super) const RETIREMENT_SQL: &str =
    include_str!("../../migrations/20261002010000_retire_chat_council.sql");

pub(super) struct CouncilFixture {
    pub(super) user_id: i64,
    pub(super) parent_id: i64,
    pub(super) selected_id: i64,
    pub(super) alternative_id: i64,
    pub(super) parent_history_id: i64,
    pub(super) council_message_id: i64,
    pub(super) selected_run_id: i64,
    pub(super) selected_followup_ids: Vec<i64>,
}

pub(super) async fn migrate_to_before_retirement(pool: &PgPool) {
    let migrations = MIGRATOR
        .iter()
        .filter(|migration| migration.version < RETIREMENT_VERSION)
        .cloned()
        .collect();
    Migrator::with_migrations(migrations)
        .run(pool)
        .await
        .expect("pre-retirement migrations should apply");
}

pub(super) async fn apply_retirement(pool: &PgPool) -> Result<(), sqlx::Error> {
    let mut transaction = pool.begin().await?;
    sqlx::query("SET LOCAL newsly.maintenance_barrier_confirmed = 'on'")
        .execute(&mut *transaction)
        .await?;
    sqlx::raw_sql(RETIREMENT_SQL)
        .execute(&mut *transaction)
        .await?;
    transaction.commit().await
}

pub(super) fn database_error_message(error: &sqlx::Error) -> String {
    error.as_database_error().map_or_else(
        || error.to_string(),
        |database| database.message().to_owned(),
    )
}

pub(super) async fn message_session(pool: &PgPool, message_id: i64) -> i64 {
    sqlx::query_scalar("SELECT session_id::bigint FROM chat_messages WHERE id::bigint = $1")
        .bind(message_id)
        .fetch_one(pool)
        .await
        .unwrap()
}

pub(super) async fn set_message_status(pool: &PgPool, message_id: i64, status: &str) {
    sqlx::query("UPDATE chat_messages SET status = $2 WHERE id::bigint = $1")
        .bind(message_id)
        .bind(status)
        .execute(pool)
        .await
        .unwrap();
}

pub(super) async fn council_snapshot(
    pool: &PgPool,
    parent_id: i64,
    child_id: i64,
) -> (i64, i64, bool) {
    sqlx::query_as(
        r#"
        SELECT
            (SELECT count(*)::bigint FROM chat_messages WHERE session_id::bigint = $1),
            (SELECT count(*)::bigint FROM chat_messages WHERE session_id::bigint = $2),
            (SELECT council_mode FROM chat_sessions WHERE id::bigint = $1)
        "#,
    )
    .bind(parent_id)
    .bind(child_id)
    .fetch_one(pool)
    .await
    .unwrap()
}

pub(super) async fn durable_messages(pool: &PgPool, ids: &[i64]) -> Vec<(i64, String, Value)> {
    sqlx::query_as(
        "SELECT id::bigint, message_list, processing_context FROM chat_messages WHERE id::bigint = ANY($1) ORDER BY id",
    )
    .bind(ids)
    .fetch_all(pool)
    .await
    .unwrap()
}

pub(super) async fn insert_user(pool: &PgPool, suffix: &str) -> i64 {
    sqlx::query_scalar(
        r#"
        INSERT INTO users (apple_id, email, is_admin, is_active, council_personas)
        VALUES ($1, $1 || '@example.test', FALSE, TRUE, $2)
        RETURNING id::bigint
        "#,
    )
    .bind(format!("council-retirement-{suffix}"))
    .bind(json!([{"id": "profile-kept", "display_name": "Kept profile"}]))
    .fetch_one(pool)
    .await
    .unwrap()
}

pub(super) async fn insert_session(
    pool: &PgPool,
    user_id: i64,
    parent_session_id: Option<i64>,
    hidden: bool,
    persona: Option<(&str, &str, &str)>,
) -> i64 {
    let (persona_id, persona_name, persona_prompt) = persona
        .map_or((None, None, None), |(id, name, prompt)| {
            (Some(id), Some(name), Some(prompt))
        });
    sqlx::query_scalar(
        r#"
        INSERT INTO chat_sessions (
            user_id, parent_session_id, title, session_type, llm_model, llm_provider,
            council_persona_id, council_persona_name, council_persona_prompt,
            council_mode, is_hidden_from_history, is_archived,
            created_at, updated_at, last_message_at
        )
        VALUES (
            $1::bigint::integer, $2::bigint::integer, 'Council retirement', 'article',
            'openai:gpt-5.6-terra', 'openai', $3, $4, $5, FALSE, $6, FALSE,
            timezone('UTC', clock_timestamp()), timezone('UTC', clock_timestamp()),
            timezone('UTC', clock_timestamp())
        )
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .bind(parent_session_id)
    .bind(persona_id)
    .bind(persona_name)
    .bind(persona_prompt)
    .bind(hidden)
    .fetch_one(pool)
    .await
    .unwrap()
}

pub(super) fn transcript(user: &str, assistant: Option<&str>, at: DateTime<Utc>) -> String {
    let mut messages = vec![NewslyMessage {
        id: None,
        role: MessageRole::User,
        parts: vec![MessagePart::Request(RequestPart::Text {
            text: user.to_owned(),
        })],
        created_at: at,
        run_id: None,
        provider: None,
        model: None,
        finish_reason: None,
        usage: ProviderUsage::default(),
        metadata: Map::new(),
    }];
    if let Some(assistant) = assistant {
        messages.push(NewslyMessage {
            id: None,
            role: MessageRole::Assistant,
            parts: vec![MessagePart::Assistant(AssistantPart::Text {
                text: assistant.to_owned(),
            })],
            created_at: at,
            run_id: None,
            provider: None,
            model: None,
            finish_reason: None,
            usage: ProviderUsage::default(),
            metadata: Map::new(),
        });
    }
    serde_json::to_string(&NewslyTranscript {
        messages,
        ..NewslyTranscript::default()
    })
    .unwrap()
}

pub(super) fn ordinary_processing_context(user_id: i64, session_id: i64, prompt: &str) -> Value {
    json!({
        "version": 1,
        "kind": "article",
        "user_prompt": prompt,
        "source": "queue",
        "session": {
            "user_id": user_id,
            "effective_session_id": session_id,
            "visible_session_id": session_id,
            "model": "openai:gpt-5.6-terra",
            "provider": "openai",
            "title": "Council retirement",
            "session_type": "article",
            "content_id": null,
            "news_item_id": null,
            "parent_session_id": null,
            "topic": null,
            "context_snapshot": null,
            "is_hidden_from_history": false,
            "council_persona_id": null,
            "council_persona_name": null,
            "council_persona_prompt": null
        },
        "screen_context": null,
        "council_run": null
    })
}

pub(super) fn council_processing_context(
    user_id: i64,
    child_id: i64,
    parent_id: i64,
    parent_message_id: i64,
    run_kind: &str,
) -> Value {
    let mut context =
        council_followup_processing_context(user_id, child_id, parent_id, "Ask the council", true);
    context["council_run"] = json!({
        "parent_message_id": parent_message_id,
        "candidate_order": 0,
        "run_kind": run_kind
    });
    context
}

pub(super) fn council_followup_processing_context(
    user_id: i64,
    child_id: i64,
    parent_id: i64,
    prompt: &str,
    explicit_null_run: bool,
) -> Value {
    let mut context = ordinary_processing_context(user_id, child_id, prompt);
    context["kind"] = json!("council");
    context["source"] = json!("council");
    context["session"]["visible_session_id"] = json!(parent_id);
    context["session"]["parent_session_id"] = json!(parent_id);
    context["session"]["is_hidden_from_history"] = json!(true);
    context["session"]["council_persona_id"] = json!("selected");
    context["session"]["council_persona_name"] = json!("Selected expert");
    context["session"]["council_persona_prompt"] = json!("Selected prompt");
    if !explicit_null_run {
        context
            .as_object_mut()
            .expect("processing context fixture should be an object")
            .remove("council_run");
    }
    context
}

pub(super) async fn insert_message(
    executor: impl Executor<'_, Database = Postgres>,
    session_id: i64,
    user: &str,
    assistant: Option<&str>,
    status: &str,
    render_metadata: Option<Value>,
    processing_context: Option<Value>,
    at: DateTime<Utc>,
) -> i64 {
    sqlx::query_scalar(
        r#"
        INSERT INTO chat_messages (
            session_id, message_list, status, render_metadata, processing_context, created_at
        )
        VALUES ($1::bigint::integer, $2, $3, $4, $5, $6)
        RETURNING id::bigint
        "#,
    )
    .bind(session_id)
    .bind(transcript(user, assistant, at))
    .bind(status)
    .bind(render_metadata)
    .bind(processing_context)
    .bind(at.naive_utc())
    .fetch_one(executor)
    .await
    .unwrap()
}

pub(super) async fn seed_council(pool: &PgPool, suffix: &str) -> CouncilFixture {
    let user_id = insert_user(pool, suffix).await;
    let parent_id = insert_session(pool, user_id, None, false, None).await;
    let selected_id = insert_session(
        pool,
        user_id,
        Some(parent_id),
        true,
        Some(("selected", "Selected expert", "Selected prompt")),
    )
    .await;
    let alternative_id = insert_session(
        pool,
        user_id,
        Some(parent_id),
        true,
        Some(("alternative", "Alternative expert", "Alternative prompt")),
    )
    .await;
    let base = Utc::now() - Duration::hours(1);
    let parent_history_id = insert_message(
        pool,
        parent_id,
        "Earlier question",
        Some("Earlier answer"),
        "completed",
        None,
        None,
        base,
    )
    .await;
    for child_id in [selected_id, alternative_id] {
        insert_message(
            pool,
            child_id,
            "Earlier question",
            Some("Earlier answer"),
            "completed",
            None,
            None,
            base,
        )
        .await;
    }

    let candidates = json!([
        {
            "persona_id": "selected",
            "persona_name": "Selected expert",
            "child_session_id": selected_id,
            "content": "Selected response",
            "status": "completed",
            "order": 0
        },
        {
            "persona_id": "alternative",
            "persona_name": "Alternative expert",
            "child_session_id": alternative_id,
            "content": "Alternative response",
            "status": "completed",
            "order": 1
        }
    ]);
    let council_message_id = insert_message(
        pool,
        parent_id,
        "Ask the council",
        Some("Selected response"),
        "completed",
        Some(json!({
            "feed_options": [],
            "council_candidates": candidates,
            "active_council_child_session_id": selected_id
        })),
        None,
        base + Duration::minutes(1),
    )
    .await;
    let selected_run_id = insert_message(
        pool,
        selected_id,
        "Ask the council",
        Some("Selected response"),
        "completed",
        None,
        Some(council_processing_context(
            user_id,
            selected_id,
            parent_id,
            council_message_id,
            "start",
        )),
        base + Duration::minutes(2),
    )
    .await;
    let selected_followup_ids = vec![
        insert_message(
            pool,
            selected_id,
            "Selected follow-up one",
            Some("Selected answer one"),
            "completed",
            None,
            Some(council_followup_processing_context(
                user_id,
                selected_id,
                parent_id,
                "Selected follow-up one",
                false,
            )),
            base + Duration::minutes(3),
        )
        .await,
        insert_message(
            pool,
            selected_id,
            "Selected follow-up two",
            Some("Selected answer two"),
            "completed",
            None,
            Some(council_followup_processing_context(
                user_id,
                selected_id,
                parent_id,
                "Selected follow-up two",
                true,
            )),
            base + Duration::minutes(4),
        )
        .await,
    ];
    let alternative_run_id = insert_message(
        pool,
        alternative_id,
        "Ask the council",
        Some("Alternative response"),
        "completed",
        None,
        Some(council_processing_context(
            user_id,
            alternative_id,
            parent_id,
            council_message_id,
            "start",
        )),
        base + Duration::minutes(2),
    )
    .await;
    sqlx::query(
        r#"
        UPDATE chat_sessions
        SET council_mode = TRUE,
            active_child_session_id = $2::bigint::integer,
            council_message_id = $3::bigint::integer,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        "#,
    )
    .bind(parent_id)
    .bind(selected_id)
    .bind(council_message_id)
    .execute(pool)
    .await
    .unwrap();
    sqlx::query(
        "UPDATE chat_sessions SET branch_start_message_id = $2::bigint::integer WHERE id::bigint = $1",
    )
    .bind(selected_id)
    .bind(selected_run_id)
    .execute(pool)
    .await
    .unwrap();
    sqlx::query(
        "UPDATE chat_sessions SET branch_start_message_id = $2::bigint::integer WHERE id::bigint = $1",
    )
    .bind(alternative_id)
    .bind(alternative_run_id)
    .execute(pool)
    .await
    .unwrap();

    CouncilFixture {
        user_id,
        parent_id,
        selected_id,
        alternative_id,
        parent_history_id,
        council_message_id,
        selected_run_id,
        selected_followup_ids,
    }
}

pub(super) async fn legacy_council(pool: &PgPool, suffix: &str) -> CouncilFixture {
    migrate_to_before_retirement(pool).await;
    seed_council(pool, suffix).await
}

pub(super) async fn assert_retired_graph(pool: &PgPool, fixture: &CouncilFixture) {
    let parent = sqlx::query_as::<_, (bool, Option<i64>, Option<i64>, bool)>(
        r#"
        SELECT council_mode, active_child_session_id::bigint,
               council_message_id::bigint, is_archived
        FROM chat_sessions WHERE id::bigint = $1
        "#,
    )
    .bind(fixture.parent_id)
    .fetch_one(pool)
    .await
    .unwrap();
    assert_eq!(parent, (false, None, None, false));

    let parent_rows = sqlx::query_as::<_, (i64, Option<Value>)>(
        "SELECT id::bigint, processing_context FROM chat_messages WHERE session_id::bigint = $1 ORDER BY created_at, id",
    )
    .bind(fixture.parent_id)
    .fetch_all(pool)
    .await
    .unwrap();
    assert_eq!(
        parent_rows.iter().map(|row| row.0).collect::<Vec<_>>(),
        [
            vec![fixture.parent_history_id, fixture.council_message_id],
            fixture.selected_followup_ids.clone()
        ]
        .concat()
    );
    assert!(parent_rows.iter().all(|(_, context)| {
        context
            .as_ref()
            .and_then(|value| value.get("council_run"))
            .is_none_or(Value::is_null)
    }));
    assert!(
        !parent_rows
            .iter()
            .any(|row| row.0 == fixture.selected_run_id)
    );
    assert_eq!(
        message_session(pool, fixture.selected_run_id).await,
        fixture.selected_id
    );

    let children = sqlx::query_as::<_, (i64, bool, bool, Option<String>, Option<String>)>(
        r#"
        SELECT id::bigint, is_hidden_from_history, is_archived,
               council_persona_id, council_persona_prompt
        FROM chat_sessions
        WHERE id::bigint = ANY($1)
        ORDER BY id
        "#,
    )
    .bind(vec![fixture.selected_id, fixture.alternative_id])
    .fetch_all(pool)
    .await
    .unwrap();
    assert_eq!(children.len(), 2);
    assert!(children.iter().all(|row| row.1 && row.2));
    assert!(
        children
            .iter()
            .all(|row| row.3.is_some() && row.4.is_some())
    );

    let retained_profile: Value =
        sqlx::query_scalar("SELECT council_personas FROM users WHERE id::bigint = $1")
            .bind(fixture.user_id)
            .fetch_one(pool)
            .await
            .unwrap();
    assert_eq!(retained_profile[0]["id"], "profile-kept");
}
