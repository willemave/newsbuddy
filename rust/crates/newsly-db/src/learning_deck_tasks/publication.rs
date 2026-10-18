//! Exact-lifecycle publication and its artifact/usage projection.
use super::{
    LearningDeckModelUsage, LearningDeckTaskRepositoryError, PublishLearningDeck,
    PublishLearningDeckOutcome, cancel_task, cancel_task_with_reason, fail_task, i64_bound,
    is_terminal, json_object, load_deck_for_update, load_task_for_update, status_entry,
    string_array,
};
use serde_json::{Map, Value, json};
use sqlx::{Postgres, Transaction};
use std::collections::HashSet;

pub async fn publish_learning_deck(
    transaction: &mut Transaction<'_, Postgres>,
    publication: &PublishLearningDeck<'_>,
) -> Result<PublishLearningDeckOutcome, LearningDeckTaskRepositoryError> {
    let Some(task) = load_task_for_update(transaction, publication.task_id).await? else {
        return Err(LearningDeckTaskRepositoryError::TaskNotFound);
    };
    if task.user_id != publication.user_id
        || task.task_kind != "learning_deck"
        || task.subject_id != Some(publication.deck_id)
    {
        return Err(LearningDeckTaskRepositoryError::OwnershipMismatch);
    }
    if is_terminal(&task.status) {
        return Ok(PublishLearningDeckOutcome::Terminal);
    }
    let Some(deck) = load_deck_for_update(transaction, publication.deck_id).await? else {
        let message = "Learning Deck not found";
        fail_task(
            transaction,
            task.id,
            "deck_not_found",
            message,
            Some(publication.sandbox_provider),
            publication.sandbox_id,
            publication.agent_log_object_key,
        )
        .await?;
        return Ok(PublishLearningDeckOutcome::Failed {
            error_type: "deck_not_found".to_owned(),
            message: message.to_owned(),
        });
    };
    if deck.deleted_at.is_some() {
        cancel_task(transaction, task.id, "Learning Deck was deleted").await?;
        return Ok(PublishLearningDeckOutcome::Cancelled);
    }

    if deck.latest_task_id != Some(task.id) || deck.user_id != task.user_id {
        cancel_task_with_reason(
            transaction,
            task.id,
            "generation_superseded",
            "Learning Deck generation is no longer current",
        )
        .await?;
        return Ok(PublishLearningDeckOutcome::Cancelled);
    }
    let user_active: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM users WHERE id::bigint = $1 AND is_active IS TRUE)",
    )
    .bind(task.user_id)
    .fetch_one(&mut **transaction)
    .await?;
    if !user_active {
        cancel_task_with_reason(
            transaction,
            task.id,
            "account_inactive",
            "Learning Deck account is no longer active",
        )
        .await?;
        return Ok(PublishLearningDeckOutcome::Cancelled);
    }

    let expired: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM learning_deck_generation_state
         WHERE llm_task_id = $1 AND deadline_at <= clock_timestamp())",
    )
    .bind(task.id)
    .fetch_one(&mut **transaction)
    .await?;
    if expired {
        let message = "Learning Deck generation deadline expired before publication";
        fail_task(
            transaction,
            task.id,
            "generation_deadline_exceeded",
            message,
            Some(publication.sandbox_provider),
            publication.sandbox_id,
            publication.agent_log_object_key,
        )
        .await?;
        return Ok(PublishLearningDeckOutcome::Failed {
            error_type: "generation_deadline_exceeded".to_owned(),
            message: message.to_owned(),
        });
    }

    let output = artifact_json(publication);
    let history = status_entry("completed", "completed", "Learning Deck is ready");
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET status = 'completed',
            workflow_state = 'completed',
            output_json = $2,
            artifact_manifest = $2,
            usage_json = $3,
            model_provider = $4,
            model_name = $5,
            sandbox_provider = $6,
            sandbox_id = $7,
            agent_log_object_key = $8,
            error_type = NULL,
            error_message = NULL,
            completed_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($9::jsonb)
        WHERE id::bigint = $1
        "#,
    )
    .bind(task.id)
    .bind(Value::Object(output))
    .bind(Value::Object(publication.usage_json.clone()))
    .bind(publication.model_provider)
    .bind(publication.model_name)
    .bind(publication.sandbox_provider)
    .bind(publication.sandbox_id)
    .bind(publication.agent_log_object_key)
    .bind(history)
    .execute(&mut **transaction)
    .await?;

    let old_keys = string_array(deck_artifact_keys(transaction, deck.id).await?);
    let mut merged_metadata = json_object(deck.source_metadata);
    merged_metadata.extend(publication.source_metadata_updates.clone());
    sqlx::query(
        r#"
        UPDATE learning_decks
        SET artifact_storage_prefix = $2,
            deck_object_key = $3,
            source_notes_object_key = $4,
            source_notes_html_object_key = $5,
            artifact_object_keys = $6,
            latest_task_id = $7::bigint::integer,
            latest_successful_task_id = $7::bigint::integer,
            source_metadata = $8,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        "#,
    )
    .bind(deck.id)
    .bind(&publication.artifact.storage_prefix)
    .bind(&publication.artifact.deck_object_key)
    .bind(&publication.artifact.source_notes_object_key)
    .bind(&publication.artifact.source_notes_html_object_key)
    .bind(json!(publication.artifact.artifact_object_keys))
    .bind(task.id)
    .bind(Value::Object(merged_metadata))
    .execute(&mut **transaction)
    .await?;

    if let Some(usage) = publication.vendor_usage {
        record_model_usage(transaction, task.id, task.user_id, usage).await?;
    }

    let new_keys = publication
        .artifact
        .artifact_object_keys
        .iter()
        .cloned()
        .collect::<HashSet<_>>();
    Ok(PublishLearningDeckOutcome::Published {
        stale_object_keys: old_keys
            .into_iter()
            .filter(|key| !new_keys.contains(key))
            .collect(),
    })
}

async fn deck_artifact_keys(
    transaction: &mut Transaction<'_, Postgres>,
    deck_id: i64,
) -> Result<Value, sqlx::Error> {
    sqlx::query_scalar("SELECT artifact_object_keys FROM learning_decks WHERE id::bigint = $1")
        .bind(deck_id)
        .fetch_one(&mut **transaction)
        .await
}

async fn record_model_usage(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    user_id: i64,
    usage: &LearningDeckModelUsage,
) -> Result<(), sqlx::Error> {
    let total = usage.input_tokens.saturating_add(usage.output_tokens);
    sqlx::query(
        r#"
        INSERT INTO vendor_usage_records (
            provider, model, feature, operation, source, request_id, task_id, user_id,
            input_tokens, output_tokens, total_tokens, request_count,
            cache_read_tokens, cache_write_tokens, currency, metadata, created_at
        )
        SELECT
            $1, $2, 'learning_deck_generation', 'learning_deck.generate', 'queue', $3,
            $4::bigint::integer, users.id,
            $5::bigint::integer, $6::bigint::integer, $7::bigint::integer,
            $8::bigint::integer, $9::bigint::integer, $10::bigint::integer,
            'USD', $11::jsonb,
            timezone('UTC', clock_timestamp())
        FROM users
        WHERE users.id::bigint = $12 AND users.is_active IS TRUE
        "#,
    )
    .bind(&usage.provider)
    .bind(&usage.model)
    .bind(&usage.provider_response_id)
    .bind(task_id)
    .bind(i64_bound(usage.input_tokens))
    .bind(i64_bound(usage.output_tokens))
    .bind(i64_bound(total))
    .bind(i64_bound(usage.request_count))
    .bind(i64_bound(usage.cache_read_tokens))
    .bind(i64_bound(usage.cache_write_tokens))
    .bind(Value::Object(usage.metadata.clone()))
    .bind(user_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

fn artifact_json(publication: &PublishLearningDeck<'_>) -> Map<String, Value> {
    Map::from_iter([
        ("deck_id".to_owned(), Value::from(publication.deck_id)),
        (
            "deck_object_key".to_owned(),
            Value::from(publication.artifact.deck_object_key.clone()),
        ),
        (
            "source_notes_object_key".to_owned(),
            Value::from(publication.artifact.source_notes_object_key.clone()),
        ),
        (
            "source_notes_html_object_key".to_owned(),
            Value::from(publication.artifact.source_notes_html_object_key.clone()),
        ),
        (
            "thumbnail_object_key".to_owned(),
            publication
                .artifact
                .thumbnail_object_key
                .clone()
                .map_or(Value::Null, Value::from),
        ),
        (
            "artifact_storage_prefix".to_owned(),
            Value::from(publication.artifact.storage_prefix.clone()),
        ),
        (
            "artifact_object_keys".to_owned(),
            json!(publication.artifact.artifact_object_keys),
        ),
        (
            "browser_validation".to_owned(),
            Value::Object(publication.browser_validation.clone()),
        ),
    ])
}

#[cfg(test)]
mod publication_lifecycle_tests {
    use super::super::StoredLearningDeckArtifact;
    use super::*;
    use sqlx::PgPool;

    async fn publication_rejected(pool: PgPool, inactive: bool) {
        crate::run_migrations(&pool).await.unwrap();
        let user: i64 = sqlx::query_scalar(
            "INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('publication-lifecycle','publication@example.com',true,false) RETURNING id::bigint",
        ).fetch_one(&pool).await.unwrap();
        let deck: i64 = sqlx::query_scalar(
            "INSERT INTO learning_decks(user_id,source_kind,source_identity,source_metadata,title,artifact_object_keys,deck_object_key,share_enabled) VALUES($1,'url','publication-source','{}','Deck','[\"published/previous.html\"]','published/previous.html',false) RETURNING id::bigint",
        ).bind(user).fetch_one(&pool).await.unwrap();
        let old_task: i64 = sqlx::query_scalar(
            "INSERT INTO llm_tasks(user_id,task_kind,mode,workflow_key,workflow_version,workflow_state,status,approval_policy,allowed_actions,tool_policy,input_json,output_json,artifact_manifest,usage_json,status_history,subject_id) VALUES($1,'learning_deck','learning_deck_presentation','learning_deck.presentation.v1',1,'running','running','{}','[]','{}','{}','{}','{}','{}','[]',$2) RETURNING id::bigint",
        ).bind(user).bind(deck).fetch_one(&pool).await.unwrap();
        let replacement: i64 = sqlx::query_scalar(
            "INSERT INTO llm_tasks(user_id,task_kind,mode,workflow_key,workflow_version,workflow_state,status,approval_policy,allowed_actions,tool_policy,input_json,output_json,artifact_manifest,usage_json,status_history,subject_id)
             SELECT user_id,task_kind,mode,workflow_key,workflow_version,'completed','completed',approval_policy,allowed_actions,tool_policy,input_json,'{}','{}','{}','[]',subject_id FROM llm_tasks WHERE id=$1 RETURNING id::bigint",
        ).bind(old_task).fetch_one(&pool).await.unwrap();
        let current = if inactive { old_task } else { replacement };
        sqlx::query("UPDATE learning_decks SET latest_task_id=$1 WHERE id=$2")
            .bind(current)
            .bind(deck)
            .execute(&pool)
            .await
            .unwrap();
        if inactive {
            sqlx::query("UPDATE users SET is_active=false WHERE id=$1")
                .bind(user)
                .execute(&pool)
                .await
                .unwrap();
        }
        let artifact = StoredLearningDeckArtifact {
            storage_prefix: "unpublished".to_owned(),
            deck_object_key: "unpublished/index.html".to_owned(),
            source_notes_object_key: "unpublished/notes.md".to_owned(),
            source_notes_html_object_key: "unpublished/notes.html".to_owned(),
            thumbnail_object_key: None,
            artifact_object_keys: vec!["unpublished/index.html".to_owned()],
        };
        let empty = Map::new();
        let mut tx = pool.begin().await.unwrap();
        let outcome = publish_learning_deck(
            &mut tx,
            &PublishLearningDeck {
                task_id: old_task,
                user_id: user,
                deck_id: deck,
                artifact: &artifact,
                browser_validation: &empty,
                source_metadata_updates: &empty,
                model_provider: "test",
                model_name: "test",
                sandbox_provider: "test",
                sandbox_id: None,
                agent_log_object_key: None,
                usage_json: &empty,
                vendor_usage: None,
            },
        )
        .await
        .unwrap();
        tx.commit().await.unwrap();
        assert_eq!(outcome, PublishLearningDeckOutcome::Cancelled);
        let (latest,key,keys):(i64,String,Value)=sqlx::query_as(
            "SELECT latest_task_id::bigint,deck_object_key,artifact_object_keys FROM learning_decks WHERE id=$1",
        ).bind(deck).fetch_one(&pool).await.unwrap();
        assert_eq!(latest, current);
        assert_eq!(key, "published/previous.html");
        assert_eq!(keys, json!(["published/previous.html"]));
        let (status, error_type): (String, String) =
            sqlx::query_as("SELECT status,error_type FROM llm_tasks WHERE id=$1")
                .bind(old_task)
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(status, "cancelled");
        assert_eq!(
            error_type,
            if inactive {
                "account_inactive"
            } else {
                "generation_superseded"
            }
        );
        let replacement_status: String =
            sqlx::query_scalar("SELECT status FROM llm_tasks WHERE id=$1")
                .bind(replacement)
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(replacement_status, "completed");
    }

    #[sqlx::test]
    async fn superseded_generation_cannot_overwrite_current_deck(pool: PgPool) {
        publication_rejected(pool, false).await;
    }

    #[sqlx::test]
    async fn inactive_account_cannot_publish_completed_generation(pool: PgPool) {
        publication_rejected(pool, true).await;
    }
}
