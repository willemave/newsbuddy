#[derive(Debug, Clone, Copy, Eq, PartialEq, Ord, PartialOrd)]
pub struct UserOwnedRelation {
    pub relation: &'static str,
    pub column: &'static str,
    pub(crate) delete_sql: &'static str,
    pub(crate) excludes_current_task: bool,
}

const fn owned_relation(
    relation: &'static str,
    column: &'static str,
    delete_sql: &'static str,
) -> UserOwnedRelation {
    UserOwnedRelation {
        relation,
        column,
        delete_sql,
        excludes_current_task: false,
    }
}

/// Explicit catalog registry. Its `PostgreSQL` test compares this set with every migrated
/// `user_id` and `owner_user_id` column, including historical tables such as
/// `daily_news_digests` that no longer have an active domain model.
pub const USER_OWNED_RELATIONS: &[UserOwnedRelation] = &[
    owned_relation(
        "analytics_interactions",
        "user_id",
        "DELETE FROM analytics_interactions WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "user_api_keys",
        "user_id",
        "DELETE FROM user_api_keys WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "consumed_refresh_tokens",
        "user_id",
        "DELETE FROM consumed_refresh_tokens WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "processing_task_user_access",
        "user_id",
        "DELETE FROM processing_task_user_access WHERE user_id::bigint = $1",
    ),
    UserOwnedRelation {
        relation: "processing_tasks",
        column: "owner_user_id",
        delete_sql: "DELETE FROM processing_tasks WHERE owner_user_id::bigint = $1 AND id::bigint <> $2",
        excludes_current_task: true,
    },
    owned_relation(
        "audio_episodes",
        "user_id",
        "DELETE FROM audio_episodes WHERE user_id::bigint = $1",
    ),
    // Delete tasks first: record_attempt holds a share lock on its task, so task deletion
    // drains in-flight attempt writes before these account-owned diagnostic rows are removed.
    owned_relation(
        "briefing_composition_attempts",
        "user_id",
        "DELETE FROM briefing_composition_attempts WHERE user_id = $1",
    ),
    owned_relation(
        "briefing_composition_cooldowns",
        "user_id",
        "DELETE FROM briefing_composition_cooldowns WHERE user_id = $1",
    ),
    owned_relation(
        "briefing_lenses",
        "user_id",
        "DELETE FROM briefing_lenses WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "briefing_segments",
        "user_id",
        "DELETE FROM briefing_segments WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "briefing_pending_sources",
        "user_id",
        "DELETE FROM briefing_pending_sources WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "briefing_states",
        "user_id",
        "DELETE FROM briefing_states WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "onboarding_first_edition_runs",
        "user_id",
        "DELETE FROM onboarding_first_edition_runs WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "chat_sessions",
        "user_id",
        "DELETE FROM chat_sessions WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "content_read_status",
        "user_id",
        "DELETE FROM content_read_status WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "content_knowledge_saves",
        "user_id",
        "DELETE FROM content_knowledge_saves WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "content_unlikes",
        "user_id",
        "DELETE FROM content_unlikes WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "content_status",
        "user_id",
        "DELETE FROM content_status WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "daily_news_digests",
        "user_id",
        "DELETE FROM daily_news_digests WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "feed_discovery_suggestions",
        "user_id",
        "DELETE FROM feed_discovery_suggestions WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "feed_discovery_runs",
        "user_id",
        "DELETE FROM feed_discovery_runs WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "user_feedback",
        "user_id",
        "DELETE FROM user_feedback WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "user_integration_connections",
        "user_id",
        "DELETE FROM user_integration_connections WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "learning_deck_runs",
        "user_id",
        "DELETE FROM learning_deck_runs WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "learning_decks",
        "user_id",
        "DELETE FROM learning_decks WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "task_sandbox_sessions",
        "user_id",
        "DELETE FROM task_sandbox_sessions WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "llm_tasks",
        "user_id",
        "DELETE FROM llm_tasks WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "news_item_read_status",
        "user_id",
        "DELETE FROM news_item_read_status WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "news_filter_decisions",
        "user_id",
        "DELETE FROM news_filter_decisions WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "news_filter_daily_usage",
        "user_id",
        "DELETE FROM news_filter_daily_usage WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "news_filter_preferences",
        "user_id",
        "DELETE FROM news_filter_preferences WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "news_items",
        "owner_user_id",
        "DELETE FROM news_items WHERE owner_user_id::bigint = $1",
    ),
    owned_relation(
        "user_news_category_schedule",
        "user_id",
        "DELETE FROM user_news_category_schedule WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "news_category_maintenance_runs",
        "user_id",
        "DELETE FROM news_category_maintenance_runs WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "news_category_candidates",
        "user_id",
        "DELETE FROM news_category_candidates WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "onboarding_discovery_suggestions",
        "user_id",
        "DELETE FROM onboarding_discovery_suggestions WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "onboarding_discovery_runs",
        "user_id",
        "DELETE FROM onboarding_discovery_runs WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "user_scraper_configs",
        "user_id",
        "DELETE FROM user_scraper_configs WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "vendor_usage_records",
        "user_id",
        "DELETE FROM vendor_usage_records WHERE user_id::bigint = $1",
    ),
];

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use sqlx::PgPool;

    use super::USER_OWNED_RELATIONS;

    // These shared attribution columns are cleared (or their session deleted) separately.
    // All other direct account references must be represented in the deletion registry.
    const SHARED_USER_REFERENCES: &[(&str, &str)] = &[
        ("cli_link_sessions", "approved_by_user_id"),
        ("llm_task_actions", "approved_by_user_id"),
        ("user_api_keys", "created_by_admin_user_id"),
    ];

    #[sqlx::test(migrations = "../newsly-db/migrations")]
    async fn registry_covers_every_direct_user_or_owner_column(pool: PgPool) {
        let discovered = sqlx::query_as::<_, (String, String)>(
            "SELECT column_name.table_name, column_name.column_name
             FROM information_schema.columns AS column_name
             JOIN information_schema.tables AS relation
               ON relation.table_schema = column_name.table_schema
              AND relation.table_name = column_name.table_name
             WHERE column_name.table_schema = 'public'
               AND relation.table_type = 'BASE TABLE'
               AND column_name.column_name LIKE '%user_id'
               AND column_name.data_type IN ('integer', 'bigint')",
        )
        .fetch_all(&pool)
        .await
        .unwrap()
        .into_iter()
        .collect::<BTreeSet<_>>();
        let mut registered = USER_OWNED_RELATIONS
            .iter()
            .map(|relation| (relation.relation.to_owned(), relation.column.to_owned()))
            .collect::<BTreeSet<_>>();
        assert_eq!(USER_OWNED_RELATIONS.len(), registered.len());
        registered.extend(
            SHARED_USER_REFERENCES
                .iter()
                .map(|(relation, column)| ((*relation).to_owned(), (*column).to_owned())),
        );
        assert_eq!(registered, discovered);
    }

    #[test]
    fn registry_covers_nightly_news_category_direct_ownership() {
        let registered = USER_OWNED_RELATIONS
            .iter()
            .map(|relation| (relation.relation, relation.column))
            .collect::<BTreeSet<_>>();
        assert!(registered.contains(&("user_news_category_schedule", "user_id")));
        assert!(registered.contains(&("news_category_maintenance_runs", "user_id")));
        assert!(registered.contains(&("news_category_candidates", "user_id")));

        // Naming attempts are owned transitively by their maintenance run, while the daily
        // naming budget is global rather than account-owned.
        assert!(!registered.iter().any(|(relation, _)| {
            matches!(
                *relation,
                "news_category_naming_attempts" | "news_category_naming_budget"
            )
        }));
    }
}
