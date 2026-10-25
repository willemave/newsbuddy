//! Every chat action uses the same queue-first fence as terminal publication.
use super::ChatToolExecutor;
use newsly_agent_runtime::AgentRuntimeError;
use sqlx::{Postgres, Transaction};

impl ChatToolExecutor {
    pub(super) async fn begin_mutation(
        &self,
    ) -> Result<Transaction<'static, Postgres>, AgentRuntimeError> {
        if tokio::time::Instant::now() >= self.dependencies.deadline {
            return Err(AgentRuntimeError::Tool(
                "The tool-work budget ended before this action could start".to_owned(),
            ));
        }
        let mut transaction = self
            .dependencies
            .pool
            .begin()
            .await
            .map_err(super::db_tool_error)?;
        let owned = self
            .dependencies
            .queue
            .lock_live_claim(&mut transaction, &self.dependencies.claim)
            .await
            .map_err(super::db_tool_error)?;
        if !owned || self.dependencies.cancellation.is_cancelled() {
            self.dependencies.cancellation.cancel();
            return Err(AgentRuntimeError::EventSink(
                "chat action lost its queue lease".to_owned(),
            ));
        }
        let current = lock_current_message(
            &mut transaction,
            self.snapshot.user_id,
            self.snapshot.session_id,
            self.snapshot.message_id,
            self.snapshot.stream_generation,
        )
        .await
        .map_err(super::db_tool_error)?;
        if !current {
            self.dependencies.cancellation.cancel();
            return Err(AgentRuntimeError::EventSink(
                "chat action was superseded".to_owned(),
            ));
        }
        if tokio::time::Instant::now() >= self.dependencies.deadline {
            return Err(AgentRuntimeError::Tool(
                "The tool-work budget ended before this action could commit".to_owned(),
            ));
        }
        Ok(transaction)
    }
}

async fn lock_current_message(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    session_id: i64,
    message_id: i64,
    generation: i32,
) -> Result<bool, sqlx::Error> {
    let current = sqlx::query_scalar::<_, i64>(
        r"
            SELECT message.id::bigint
            FROM chat_messages AS message
            JOIN chat_sessions AS session ON session.id = message.session_id
            JOIN users AS account ON account.id = session.user_id
            WHERE message.id::bigint = $1 AND session.id::bigint = $2
                AND session.user_id::bigint = $3 AND account.is_active = TRUE
                AND session.is_archived = FALSE AND message.status = 'processing'
                AND message.stream_generation = $4
            FOR SHARE OF message, session, account
        ",
    )
    .bind(message_id)
    .bind(session_id)
    .bind(user_id)
    .bind(generation)
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(current.is_some())
}

#[cfg(test)]
mod tests {
    use super::*;
    use sqlx::PgPool;
    #[sqlx::test(migrations = "../newsly-db/migrations")]
    async fn mutation_message_fence_requires_current_active_owner_and_generation(pool: PgPool) {
        let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('chat-fence-owner','chat-fence-owner@example.test',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
        let session: i64 = sqlx::query_scalar("INSERT INTO chat_sessions(user_id,title,session_type,llm_provider,llm_model) VALUES($1,'Fence','knowledge_chat','openai','openai:gpt-6.1-sol') RETURNING id::bigint").bind(user).fetch_one(&pool).await.unwrap();
        let message:i64 = sqlx::query_scalar("INSERT INTO chat_messages(session_id,message_list,status,stream_generation) VALUES($1,'[]','processing',3) RETURNING id::bigint").bind(session).fetch_one(&pool).await.unwrap();
        let mut tx = pool.begin().await.unwrap();
        assert!(
            lock_current_message(&mut tx, user, session, message, 3)
                .await
                .unwrap()
        );
        assert!(
            !lock_current_message(&mut tx, user, session, message, 2)
                .await
                .unwrap()
        );
        assert!(
            !lock_current_message(&mut tx, user + 1, session, message, 3)
                .await
                .unwrap()
        );
        tx.rollback().await.unwrap();
        sqlx::query("UPDATE chat_sessions SET is_archived=true WHERE id::bigint=$1")
            .bind(session)
            .execute(&pool)
            .await
            .unwrap();
        let mut tx = pool.begin().await.unwrap();
        assert!(
            !lock_current_message(&mut tx, user, session, message, 3)
                .await
                .unwrap()
        );
        tx.rollback().await.unwrap();
        sqlx::query("UPDATE chat_sessions SET is_archived=false WHERE id::bigint=$1")
            .bind(session)
            .execute(&pool)
            .await
            .unwrap();
        sqlx::query("UPDATE users SET is_active=false WHERE id::bigint=$1")
            .bind(user)
            .execute(&pool)
            .await
            .unwrap();
        let mut tx = pool.begin().await.unwrap();
        assert!(
            !lock_current_message(&mut tx, user, session, message, 3)
                .await
                .unwrap()
        );
    }
}
