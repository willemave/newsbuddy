use newsly_agent_runtime::{NewslyTranscript, TranscriptError};
use serde::Serialize;
use serde_json::Value;
use sqlx::{FromRow, PgPool};
use thiserror::Error;

use crate::chat_transcripts::{decode_transcript, is_legacy_transcript_shape};

const MAX_AUDIT_ROWS: i64 = 10_000;
const MAX_APPLY_ROWS: i64 = 1_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChatTranscriptCompatibility {
    Canonical,
    LegacyShape,
    Invalid,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ChatTranscriptCompatibilityPage {
    pub after_id: i64,
    pub limit: i64,
    pub scanned_count: usize,
    pub canonical_count: usize,
    pub legacy_shape_count: usize,
    pub invalid_count: usize,
    pub last_id: Option<i64>,
    pub more: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ChatTranscriptBackfillPage {
    pub after_id: i64,
    pub limit: i64,
    pub applied: bool,
    pub scanned_count: usize,
    pub canonical_count: usize,
    pub legacy_shape_count: usize,
    pub invalid_count: usize,
    pub updated_count: usize,
    pub conflict_count: usize,
    pub last_id: Option<i64>,
    pub last_applied_id: Option<i64>,
    pub more: bool,
}

#[derive(Debug, FromRow)]
struct TranscriptAuditRow {
    id: i64,
    message_list: String,
}

/// Classifies one stored transcript without returning any transcript payload.
pub fn classify_chat_transcript_compatibility(raw: &str) -> ChatTranscriptCompatibility {
    match classify_transcript(raw) {
        ClassifiedTranscript::Canonical => ChatTranscriptCompatibility::Canonical,
        ClassifiedTranscript::Legacy(_) => ChatTranscriptCompatibility::LegacyShape,
        ClassifiedTranscript::Invalid => ChatTranscriptCompatibility::Invalid,
    }
}

/// Counts compatibility classes for one bounded page of stored chat transcripts.
///
/// # Errors
///
/// Returns a validation error for a negative cursor or an out-of-range limit, and a database
/// error when the read-only page query fails.
pub async fn audit_chat_transcript_compatibility(
    pool: &PgPool,
    after_id: i64,
    limit: i64,
) -> Result<ChatTranscriptCompatibilityPage, ChatTranscriptCompatibilityAuditError> {
    validate_page(after_id, limit)?;
    let rows = load_audit_rows(pool, after_id, limit)
        .await
        .map_err(|error| audit_database_error(&error))?;
    Ok(compatibility_page(after_id, limit, rows))
}

/// Converts one bounded page of legacy-shape transcripts to the canonical wire format.
///
/// Dry-run is the default when `apply` is false. Applying uses one autocommit compare-and-swap
/// update per eligible row and never changes sessions, status, or other message columns.
///
/// # Errors
///
/// Returns a validation error for a negative cursor or out-of-range limit, a fixed invariant error
/// if a converted transcript cannot round-trip exactly, or a redacted database error.
pub async fn backfill_chat_transcript_compatibility(
    pool: &PgPool,
    after_id: i64,
    limit: i64,
    apply: bool,
) -> Result<ChatTranscriptBackfillPage, ChatTranscriptBackfillError> {
    validate_page(after_id, limit).map_err(ChatTranscriptBackfillError::Validation)?;
    if apply && limit > MAX_APPLY_ROWS {
        return Err(ChatTranscriptBackfillError::ApplyLimit {
            max: MAX_APPLY_ROWS,
        });
    }
    let mut rows = load_audit_rows(pool, after_id, limit)
        .await
        .map_err(|error| backfill_database_error(&error, None, None))?;
    let more = rows.len() > usize::try_from(limit).unwrap_or(usize::MAX);
    rows.truncate(usize::try_from(limit).unwrap_or(usize::MAX));
    let mut page = ChatTranscriptBackfillPage {
        after_id,
        limit,
        applied: apply,
        scanned_count: rows.len(),
        canonical_count: 0,
        legacy_shape_count: 0,
        invalid_count: 0,
        updated_count: 0,
        conflict_count: 0,
        last_id: rows.last().map(|row| row.id),
        last_applied_id: None,
        more,
    };
    for row in rows {
        match classify_transcript(&row.message_list) {
            ClassifiedTranscript::Canonical => page.canonical_count += 1,
            ClassifiedTranscript::Invalid => page.invalid_count += 1,
            ClassifiedTranscript::Legacy(converted) => {
                page.legacy_shape_count += 1;
                let canonical = canonical_round_trip(&converted).map_err(|()| {
                    ChatTranscriptBackfillError::Invariant {
                        row_id: row.id,
                        last_applied_id: page.last_applied_id,
                    }
                })?;
                if !apply {
                    continue;
                }
                let updated =
                    apply_chat_transcript_cas(pool, row.id, &row.message_list, &canonical)
                        .await
                        .map_err(|error| {
                            backfill_database_error(&error, Some(row.id), page.last_applied_id)
                        })?;
                if updated {
                    page.updated_count += 1;
                    page.last_applied_id = Some(row.id);
                } else {
                    page.conflict_count += 1;
                }
            }
        }
    }
    Ok(page)
}

async fn load_audit_rows(
    pool: &PgPool,
    after_id: i64,
    limit: i64,
) -> Result<Vec<TranscriptAuditRow>, sqlx::Error> {
    sqlx::query_as::<_, TranscriptAuditRow>(
        r"
        SELECT id::bigint AS id, message_list
        FROM chat_messages AS m
        WHERE m.id > $1
        ORDER BY m.id
        LIMIT $2
        ",
    )
    .bind(after_id)
    .bind(limit + 1)
    .fetch_all(pool)
    .await
}

fn compatibility_page(
    after_id: i64,
    limit: i64,
    mut rows: Vec<TranscriptAuditRow>,
) -> ChatTranscriptCompatibilityPage {
    let more = rows.len() > usize::try_from(limit).unwrap_or(usize::MAX);
    rows.truncate(usize::try_from(limit).unwrap_or(usize::MAX));

    let mut page = ChatTranscriptCompatibilityPage {
        after_id,
        limit,
        scanned_count: rows.len(),
        canonical_count: 0,
        legacy_shape_count: 0,
        invalid_count: 0,
        last_id: rows.last().map(|row| row.id),
        more,
    };
    for row in rows {
        match classify_chat_transcript_compatibility(&row.message_list) {
            ChatTranscriptCompatibility::Canonical => page.canonical_count += 1,
            ChatTranscriptCompatibility::LegacyShape => page.legacy_shape_count += 1,
            ChatTranscriptCompatibility::Invalid => page.invalid_count += 1,
        }
    }
    page
}

fn validate_page(after_id: i64, limit: i64) -> Result<(), ChatTranscriptCompatibilityAuditError> {
    if after_id < 0 {
        return Err(ChatTranscriptCompatibilityAuditError::InvalidAfterId);
    }
    if !(1..=MAX_AUDIT_ROWS).contains(&limit) {
        return Err(ChatTranscriptCompatibilityAuditError::InvalidLimit {
            max: MAX_AUDIT_ROWS,
        });
    }
    Ok(())
}

enum ClassifiedTranscript {
    Canonical,
    Legacy(NewslyTranscript),
    Invalid,
}

fn classify_transcript(raw: &str) -> ClassifiedTranscript {
    let Ok(value) = serde_json::from_str::<Value>(raw) else {
        return ClassifiedTranscript::Invalid;
    };
    if strict_transcript(&value).is_ok() {
        return ClassifiedTranscript::Canonical;
    }
    if is_legacy_transcript_shape(&value)
        && let Ok(transcript) = decode_transcript(raw)
    {
        return ClassifiedTranscript::Legacy(transcript);
    }
    ClassifiedTranscript::Invalid
}

fn canonical_round_trip(transcript: &NewslyTranscript) -> Result<String, ()> {
    let canonical = serde_json::to_string(transcript).map_err(|_| ())?;
    let round_trip = serde_json::from_str::<NewslyTranscript>(&canonical).map_err(|_| ())?;
    round_trip.validate().map_err(|_| ())?;
    if &round_trip != transcript {
        return Err(());
    }
    Ok(canonical)
}

async fn apply_chat_transcript_cas(
    pool: &PgPool,
    row_id: i64,
    observed: &str,
    canonical: &str,
) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query(
        r"
        UPDATE chat_messages
        SET message_list = $1
        WHERE id = $2
          AND message_list = $3
          AND status <> 'processing'
        ",
    )
    .bind(canonical)
    .bind(row_id)
    .bind(observed)
    .execute(pool)
    .await?
    .rows_affected()
        == 1)
}

fn strict_transcript(value: &Value) -> Result<(), StrictTranscriptError> {
    let transcript = serde_json::from_value::<NewslyTranscript>(value.clone())?;
    transcript.validate()?;
    Ok(())
}

#[derive(Debug, Error)]
enum StrictTranscriptError {
    #[error("chat transcript JSON does not match the canonical schema")]
    Json(#[from] serde_json::Error),
    #[error("canonical chat transcript is invalid")]
    Transcript(#[from] TranscriptError),
}

#[derive(Debug, Error)]
pub enum ChatTranscriptCompatibilityAuditError {
    #[error("after_id must be non-negative")]
    InvalidAfterId,
    #[error("limit must be between 1 and {max}")]
    InvalidLimit { max: i64 },
    #[error("chat transcript compatibility query failed (database code {code})")]
    Database { code: String },
}

#[derive(Debug, Error)]
pub enum ChatTranscriptBackfillError {
    #[error("{0}")]
    Validation(ChatTranscriptCompatibilityAuditError),
    #[error("apply limit must be between 1 and {max}")]
    ApplyLimit { max: i64 },
    #[error(
        "chat transcript canonicalization invariant failed at row {row_id}; last applied row {last_applied_id:?}"
    )]
    Invariant {
        row_id: i64,
        last_applied_id: Option<i64>,
    },
    #[error(
        "chat transcript backfill database operation failed at row {row_id:?}; last applied row {last_applied_id:?}; database code {code}"
    )]
    Database {
        row_id: Option<i64>,
        last_applied_id: Option<i64>,
        code: String,
    },
}

fn audit_database_error(error: &sqlx::Error) -> ChatTranscriptCompatibilityAuditError {
    ChatTranscriptCompatibilityAuditError::Database {
        code: database_code(error),
    }
}

fn backfill_database_error(
    error: &sqlx::Error,
    row_id: Option<i64>,
    last_applied_id: Option<i64>,
) -> ChatTranscriptBackfillError {
    ChatTranscriptBackfillError::Database {
        row_id,
        last_applied_id,
        code: database_code(error),
    }
}

fn database_code(error: &sqlx::Error) -> String {
    error
        .as_database_error()
        .and_then(sqlx::error::DatabaseError::code)
        .map_or_else(|| "unavailable".to_owned(), std::borrow::Cow::into_owned)
}

#[cfg(test)]
mod tests {
    use chrono::{TimeZone as _, Utc};
    use newsly_agent_runtime::{
        MessagePart, MessageRole, NewslyMessage, NewslyTranscript, ProviderUsage, RequestPart,
    };
    use serde_json::{Map, json};
    use sqlx::PgPool;

    use super::{
        ChatTranscriptCompatibility, apply_chat_transcript_cas,
        audit_chat_transcript_compatibility, backfill_chat_transcript_compatibility,
        classify_chat_transcript_compatibility,
    };

    #[test]
    fn classification_distinguishes_canonical_legacy_and_invalid_rows() {
        let created_at = Utc.with_ymd_and_hms(2026, 10, 3, 12, 0, 0).unwrap();
        let canonical = NewslyTranscript {
            messages: vec![NewslyMessage {
                id: None,
                role: MessageRole::User,
                parts: vec![MessagePart::Request(RequestPart::Text {
                    text: "Hello".to_owned(),
                })],
                created_at,
                run_id: None,
                provider: None,
                model: None,
                finish_reason: None,
                usage: ProviderUsage::default(),
                metadata: Map::new(),
            }],
            ..NewslyTranscript::default()
        };
        assert_eq!(
            classify_chat_transcript_compatibility(&serde_json::to_string(&canonical).unwrap()),
            ChatTranscriptCompatibility::Canonical
        );
        assert_eq!(
            classify_chat_transcript_compatibility(
                &json!([{
                    "kind": "request",
                    "parts": [{"part_kind": "user-prompt", "content": "Hello"}],
                    "timestamp": "2026-10-03T12:00:00Z"
                }])
                .to_string()
            ),
            ChatTranscriptCompatibility::LegacyShape
        );
        assert_eq!(
            classify_chat_transcript_compatibility("{not-json"),
            ChatTranscriptCompatibility::Invalid
        );
        assert_eq!(
            classify_chat_transcript_compatibility(r#"{"version":1,"messages":{}}"#),
            ChatTranscriptCompatibility::Invalid
        );
    }

    #[sqlx::test]
    async fn audit_pages_without_returning_transcript_payloads(pool: PgPool) {
        let user_id: i64 = sqlx::query_scalar(
            "INSERT INTO users(apple_id,email,is_admin,is_active) VALUES('transcript-audit','transcript-audit@example.test',false,true) RETURNING id::bigint",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        let session_id: i64 = sqlx::query_scalar(
            "INSERT INTO chat_sessions(user_id,title,session_type,llm_model,llm_provider) VALUES($1,'Audit','knowledge_chat','openai:gpt-5.6-sol','openai') RETURNING id::bigint",
        )
        .bind(user_id)
        .fetch_one(&pool)
        .await
        .unwrap();
        let canonical = serde_json::to_string(&crate::chat_transcripts::processing_transcript(
            "Hello",
            Utc.with_ymd_and_hms(2026, 10, 3, 12, 0, 0).unwrap(),
        ))
        .unwrap();
        let legacy = json!([{
            "kind": "request",
            "parts": [{"part_kind": "user-prompt", "content": "Hello"}],
            "timestamp": "2026-10-03T12:00:00Z"
        }])
        .to_string();
        let mut message_ids = Vec::new();
        for transcript in [canonical.as_str(), legacy.as_str(), "{not-json"] {
            let id: i64 = sqlx::query_scalar(
                "INSERT INTO chat_messages(session_id,message_list,status) VALUES($1,$2,'completed') RETURNING id::bigint",
            )
            .bind(session_id)
            .bind(transcript)
            .fetch_one(&pool)
            .await
            .unwrap();
            message_ids.push(id);
        }

        let first = audit_chat_transcript_compatibility(&pool, 0, 2)
            .await
            .unwrap();
        assert_eq!(first.scanned_count, 2);
        assert_eq!(first.canonical_count, 1);
        assert_eq!(first.legacy_shape_count, 1);
        assert_eq!(first.invalid_count, 0);
        assert!(first.more);
        let second = audit_chat_transcript_compatibility(&pool, first.last_id.unwrap(), 2)
            .await
            .unwrap();
        assert_eq!(second.scanned_count, 1);
        assert_eq!(second.canonical_count, 0);
        assert_eq!(second.legacy_shape_count, 0);
        assert_eq!(second.invalid_count, 1);
        assert!(!second.more);
        assert!(serde_json::to_value(second).unwrap().get("rows").is_none());

        let dry_run = backfill_chat_transcript_compatibility(&pool, 0, 10, false)
            .await
            .unwrap();
        assert_eq!(dry_run.legacy_shape_count, 1);
        assert_eq!(dry_run.updated_count, 0);
        let receipt = serde_json::to_string(&dry_run).unwrap();
        assert!(!receipt.contains("message_list"));
        assert!(!receipt.contains("Hello"));
        let stored: String =
            sqlx::query_scalar("SELECT message_list FROM chat_messages WHERE id = $1")
                .bind(message_ids[1])
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(stored, legacy);

        let applied = backfill_chat_transcript_compatibility(&pool, 0, 10, true)
            .await
            .unwrap();
        assert_eq!(applied.updated_count, 1);
        assert_eq!(applied.conflict_count, 0);
        assert_eq!(applied.last_applied_id, Some(message_ids[1]));
        let after_apply = audit_chat_transcript_compatibility(&pool, 0, 10)
            .await
            .unwrap();
        assert_eq!(after_apply.canonical_count, 2);
        assert_eq!(after_apply.legacy_shape_count, 0);
        assert_eq!(after_apply.invalid_count, 1);
        let repeated = backfill_chat_transcript_compatibility(&pool, 0, 10, true)
            .await
            .unwrap();
        assert_eq!(repeated.updated_count, 0);
        assert_eq!(repeated.canonical_count, 2);

        let stale_id: i64 = sqlx::query_scalar(
            "INSERT INTO chat_messages(session_id,message_list,status) VALUES($1,$2,'completed') RETURNING id::bigint",
        )
        .bind(session_id)
        .bind(&legacy)
        .fetch_one(&pool)
        .await
        .unwrap();
        sqlx::query("UPDATE chat_messages SET message_list = '{newer-invalid' WHERE id = $1")
            .bind(stale_id)
            .execute(&pool)
            .await
            .unwrap();
        assert!(
            !apply_chat_transcript_cas(&pool, stale_id, &legacy, &canonical)
                .await
                .unwrap()
        );
        let newer: String =
            sqlx::query_scalar("SELECT message_list FROM chat_messages WHERE id = $1")
                .bind(stale_id)
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(newer, "{newer-invalid");

        let processing_id: i64 = sqlx::query_scalar(
            "INSERT INTO chat_messages(session_id,message_list,status) VALUES($1,$2,'processing') RETURNING id::bigint",
        )
        .bind(session_id)
        .bind(&legacy)
        .fetch_one(&pool)
        .await
        .unwrap();
        assert!(
            !apply_chat_transcript_cas(&pool, processing_id, &legacy, &canonical)
                .await
                .unwrap()
        );
        let processing_page = backfill_chat_transcript_compatibility(&pool, stale_id, 10, true)
            .await
            .unwrap();
        assert_eq!(processing_page.legacy_shape_count, 1);
        assert_eq!(processing_page.updated_count, 0);
        assert_eq!(processing_page.conflict_count, 1);
    }

    #[sqlx::test]
    async fn recorded_batch_can_resume_a_partially_applied_backfill(pool: PgPool) {
        let user_id: i64 = sqlx::query_scalar(
            "INSERT INTO users(apple_id,email,is_admin,is_active) VALUES('transcript-resume','transcript-resume@example.test',false,true) RETURNING id::bigint",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        let session_id: i64 = sqlx::query_scalar(
            "INSERT INTO chat_sessions(user_id,title,session_type,llm_model,llm_provider) VALUES($1,'Resume','knowledge_chat','openai:gpt-5.6-sol','openai') RETURNING id::bigint",
        )
        .bind(user_id)
        .fetch_one(&pool)
        .await
        .unwrap();
        let legacy = json!([{
            "kind": "request",
            "parts": [{"part_kind": "user-prompt", "content": "Hello"}],
            "timestamp": "2026-10-03T12:00:00Z"
        }])
        .to_string();
        for _ in 0..2 {
            sqlx::query(
                "INSERT INTO chat_messages(session_id,message_list,status) VALUES($1,$2,'completed')",
            )
            .bind(session_id)
            .bind(&legacy)
            .execute(&pool)
            .await
            .unwrap();
        }

        let batch_id = uuid::Uuid::new_v4();
        let targets = json!({"after_id": 0, "limit": 10});
        let mut transaction = pool.begin().await.unwrap();
        assert!(
            crate::incident_batches::record_batch(
                &mut transaction,
                batch_id,
                "chat_transcript_backfill",
                "ops",
                "canonicalize legacy transcripts",
                &targets,
            )
            .await
            .unwrap()
        );
        transaction.commit().await.unwrap();

        let partial = backfill_chat_transcript_compatibility(&pool, 0, 1, true)
            .await
            .unwrap();
        assert_eq!(partial.updated_count, 1);
        assert!(partial.more);

        let mut transaction = pool.begin().await.unwrap();
        assert!(
            !crate::incident_batches::record_batch(
                &mut transaction,
                batch_id,
                "chat_transcript_backfill",
                "ops",
                "canonicalize legacy transcripts",
                &targets,
            )
            .await
            .unwrap()
        );
        transaction.commit().await.unwrap();

        let resumed = backfill_chat_transcript_compatibility(&pool, 0, 10, true)
            .await
            .unwrap();
        assert_eq!(resumed.canonical_count, 1);
        assert_eq!(resumed.legacy_shape_count, 1);
        assert_eq!(resumed.updated_count, 1);
        let after_resume = audit_chat_transcript_compatibility(&pool, 0, 10)
            .await
            .unwrap();
        assert_eq!(after_resume.canonical_count, 2);
        assert_eq!(after_resume.legacy_shape_count, 0);

        let mut transaction = pool.begin().await.unwrap();
        assert!(
            crate::incident_batches::record_batch(
                &mut transaction,
                batch_id,
                "chat_transcript_backfill",
                "different-actor",
                "canonicalize legacy transcripts",
                &targets,
            )
            .await
            .is_err()
        );
    }
}
