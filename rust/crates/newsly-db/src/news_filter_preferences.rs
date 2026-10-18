//! Latest immutable revision is canonical. Lock the user to serialize edits and initialization.
use sqlx::{FromRow, PgConnection, Postgres, Transaction};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq, FromRow, Default)]
pub struct NewsFilterPreferenceRecord {
    pub revision: i64,
    pub text: String,
    pub enabled: bool,
}

#[derive(Debug, Error)]
pub enum NewsFilterPreferenceError {
    #[error("news filter revision changed; reload before saving")]
    Conflict,
    #[error("user missing or inactive")]
    UserMissingOrInactive,
    #[error("news filter text exceeds 8000 characters")]
    TextTooLong,
    #[error("enabled news filter exceeds the conservative 8192-byte inference bound")]
    ActivationTooLong,
    #[error(transparent)]
    Database(#[from] sqlx::Error),
}

pub fn validate(text: &str, enabled: bool) -> Result<bool, NewsFilterPreferenceError> {
    if text.chars().count() > 8_000 {
        return Err(NewsFilterPreferenceError::TextTooLong);
    }
    let enabled = enabled && !text.trim().is_empty();
    if enabled && text.len() > 8_192 {
        return Err(NewsFilterPreferenceError::ActivationTooLong);
    }
    Ok(enabled)
}

pub async fn load_current(
    connection: &mut PgConnection,
    user_id: i64,
) -> Result<NewsFilterPreferenceRecord, sqlx::Error> {
    Ok(sqlx::query_as::<_, NewsFilterPreferenceRecord>(
        "SELECT revision, text, enabled FROM news_filter_preferences WHERE user_id = $1::bigint::integer ORDER BY revision DESC LIMIT 1"
    ).bind(user_id).fetch_optional(connection).await?.unwrap_or_default())
}

async fn lock_user(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<(), NewsFilterPreferenceError> {
    let user = sqlx::query_scalar::<_, i64>("SELECT id::bigint FROM users WHERE id = $1::bigint::integer AND is_active = TRUE FOR UPDATE")
        .bind(user_id).fetch_optional(&mut **transaction).await?;
    if user.is_none() {
        return Err(NewsFilterPreferenceError::UserMissingOrInactive);
    }
    Ok(())
}

pub async fn save(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    expected_revision: i64,
    text: &str,
    enabled: bool,
) -> Result<NewsFilterPreferenceRecord, NewsFilterPreferenceError> {
    let enabled = validate(text, enabled)?;
    lock_user(transaction, user_id).await?;
    let current = load_current(transaction, user_id).await?;
    if current.revision != expected_revision {
        return Err(NewsFilterPreferenceError::Conflict);
    }
    // Preserve cache identity when Apply resubmits the same saved preference.
    // A revision-zero clear must still persist the person's choice against onboarding.
    if current.revision > 0 && current.text == text && current.enabled == enabled {
        return Ok(current);
    }
    let saved = insert(
        transaction,
        user_id,
        current.revision + 1,
        text,
        enabled,
        None,
    )
    .await?;
    sqlx::query(
        "DELETE FROM briefing_pending_sources AS pending WHERE pending.user_id = $1::bigint::integer AND pending.source_kind = 'news' AND NOT EXISTS (SELECT 1 FROM briefing_segments AS segment WHERE segment.user_id = pending.user_id AND segment.status IN ('active', 'degraded') AND segment.source_keys::jsonb ? ('news:' || pending.source_id::text))"
    ).bind(user_id).execute(&mut **transaction).await?;
    Ok(saved)
}

/// Preserves over-token drafts disabled, and never overwrites an existing choice.
pub async fn initialize(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    text: &str,
    enabled: bool,
    input_kind: Option<&str>,
    source_run_id: i64,
) -> Result<NewsFilterPreferenceRecord, NewsFilterPreferenceError> {
    let enabled = match validate(text, enabled) {
        Ok(enabled) => enabled,
        Err(NewsFilterPreferenceError::ActivationTooLong) => false,
        Err(error) => return Err(error),
    };
    lock_user(transaction, user_id).await?;
    let current = load_current(transaction, user_id).await?;
    if current.revision != 0 {
        return Ok(current);
    }
    insert(
        transaction,
        user_id,
        1,
        text,
        enabled,
        Some((input_kind, source_run_id)),
    )
    .await
}

async fn insert(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    revision: i64,
    text: &str,
    enabled: bool,
    provenance: Option<(Option<&str>, i64)>,
) -> Result<NewsFilterPreferenceRecord, NewsFilterPreferenceError> {
    Ok(sqlx::query_as::<_, NewsFilterPreferenceRecord>(
        "INSERT INTO news_filter_preferences (user_id, revision, text, enabled, origin, input_kind, source_run_id) VALUES ($1::bigint::integer,$2,$3,$4,$5,$6,$7::bigint::integer) RETURNING revision,text,enabled"
    ).bind(user_id).bind(revision).bind(text).bind(enabled)
        .bind(if provenance.is_some() { "onboarding" } else { "settings" })
        .bind(provenance.and_then(|p| p.0)).bind(provenance.map(|p| p.1))
        .fetch_one(&mut **transaction).await?)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn validation_never_truncates_or_activates_blank_or_oversized_text() {
        assert!(!validate("  \n", true).unwrap());
        assert!(matches!(
            validate(&"a".repeat(8001), false),
            Err(NewsFilterPreferenceError::TextTooLong)
        ));
        assert!(matches!(
            validate(&"界".repeat(3000), true),
            Err(NewsFilterPreferenceError::ActivationTooLong)
        ));
        assert!(!validate(&"界".repeat(3000), false).unwrap());
        assert!(validate("AI research, exclude funding", true).unwrap());
    }
}

#[cfg(test)]
mod persistence_tests {
    use super::*;
    use sqlx::PgPool;

    pub(super) async fn schema(pool: &PgPool) {
        sqlx::raw_sql("CREATE TABLE users(id integer PRIMARY KEY, is_active boolean NOT NULL); CREATE TABLE onboarding_discovery_runs(id integer PRIMARY KEY); CREATE TABLE briefing_pending_sources(user_id integer, source_kind text, source_id bigint); CREATE TABLE briefing_segments(user_id integer, status text, source_keys jsonb); INSERT INTO users VALUES(1,true),(2,true); INSERT INTO onboarding_discovery_runs VALUES(1);")
            .execute(pool).await.unwrap();
        let migration = include_str!("../migrations/20261017030000_news_filter_preferences.sql");
        sqlx::raw_sql(migration).execute(pool).await.unwrap();
    }

    #[sqlx::test(migrations = false)]
    async fn concurrent_saves_reject_lost_update_and_onboarding_preserves_clear(pool: PgPool) {
        schema(&pool).await;
        let mut first = pool.begin().await.unwrap();
        let saved = save(&mut first, 1, 0, "", true).await.unwrap();
        assert_eq!(saved.revision, 1);
        assert!(!saved.enabled);
        let other_pool = pool.clone();
        let concurrent = tokio::spawn(async move {
            let mut second = other_pool.begin().await.unwrap();
            save(&mut second, 1, 0, "finance", true).await
        });
        first.commit().await.unwrap();
        assert!(matches!(
            concurrent.await.unwrap(),
            Err(NewsFilterPreferenceError::Conflict)
        ));
        let mut tx = pool.begin().await.unwrap();
        let retained = initialize(&mut tx, 1, "AI", true, Some("typed"), 1)
            .await
            .unwrap();
        assert_eq!(retained, saved);
        tx.commit().await.unwrap();
        let count = sqlx::query_scalar::<_, i64>(
            "SELECT count(*) FROM news_filter_preferences WHERE user_id=1",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(count, 1);
    }

    #[sqlx::test(migrations = false)]
    async fn onboarding_retains_exact_text_and_disables_over_token_draft(pool: PgPool) {
        schema(&pool).await;
        let exact = "  Interested in AI, NOT funding.\n";
        let mut tx = pool.begin().await.unwrap();
        let first = initialize(&mut tx, 1, exact, true, Some("speech_transcript"), 1)
            .await
            .unwrap();
        assert_eq!(first.text, exact);
        assert!(first.enabled);
        let oversized = "界".repeat(3000);
        let second = initialize(&mut tx, 2, &oversized, true, Some("typed"), 1)
            .await
            .unwrap();
        assert_eq!(second.text, oversized);
        assert!(!second.enabled);
        tx.commit().await.unwrap();
        sqlx::query("DELETE FROM users WHERE id=1")
            .execute(&pool)
            .await
            .unwrap();
        let mut tx = pool.begin().await.unwrap();
        assert!(matches!(
            save(&mut tx, 1, 1, "new", true).await,
            Err(NewsFilterPreferenceError::UserMissingOrInactive)
        ));
    }
}

#[cfg(test)]
mod pending_tests {
    use super::*;
    #[sqlx::test(migrations = false)]
    async fn save_rechecks_only_unpublished_news(pool: sqlx::PgPool) {
        super::persistence_tests::schema(&pool).await;
        sqlx::raw_sql(r#"INSERT INTO briefing_pending_sources VALUES (1,'news',1),(1,'news',2),(1,'content',1),(2,'news',1); INSERT INTO briefing_segments VALUES (1,'active','["news:2"]');"#).execute(&pool).await.unwrap();
        let mut tx = pool.begin().await.unwrap();
        save(&mut tx, 1, 0, "AI", true).await.unwrap();
        let unchanged = save(&mut tx, 1, 1, "AI", true).await.unwrap();
        assert_eq!(unchanged.revision, 1);
        tx.commit().await.unwrap();
        let remaining = sqlx::query_as::<_, (i32,String,i64)>("SELECT user_id,source_kind,source_id FROM briefing_pending_sources ORDER BY user_id,source_kind,source_id").fetch_all(&pool).await.unwrap();
        assert_eq!(
            remaining,
            vec![
                (1, "content".into(), 1),
                (1, "news".into(), 2),
                (2, "news".into(), 1)
            ]
        );
    }
}
