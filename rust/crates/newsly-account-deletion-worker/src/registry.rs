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

/// Explicit catalog registry. Its fixture test compares this set with every baseline `user_id`
/// and `owner_user_id` column, including historical tables such as `daily_news_digests` that no
/// longer have an active domain model.
pub const USER_OWNED_RELATIONS: &[UserOwnedRelation] = &[
    owned_relation(
        "analytics_interactions",
        "user_id",
        "DELETE FROM analytics_interactions WHERE user_id::bigint = $1",
    ),
    owned_relation(
        "agent_data_files",
        "user_id",
        "DELETE FROM agent_data_files WHERE user_id::bigint = $1",
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
        "news_items",
        "owner_user_id",
        "DELETE FROM news_items WHERE owner_user_id::bigint = $1",
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

    use serde::Deserialize;

    use super::USER_OWNED_RELATIONS;

    #[derive(Debug, Deserialize)]
    struct CatalogInventory {
        columns: Vec<CatalogColumn>,
    }

    #[derive(Debug, Deserialize)]
    struct CatalogColumn {
        relation: String,
        name: String,
    }

    #[test]
    fn registry_covers_every_direct_user_or_owner_column() {
        let inventory: CatalogInventory = serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../newsly-db/baseline/catalog-inventory.json"
        )))
        .unwrap();
        let discovered = inventory
            .columns
            .into_iter()
            .filter(|column| matches!(column.name.as_str(), "user_id" | "owner_user_id"))
            .map(|column| (column.relation, column.name))
            .collect::<BTreeSet<_>>();
        let registered = USER_OWNED_RELATIONS
            .iter()
            .map(|relation| (relation.relation.to_owned(), relation.column.to_owned()))
            .collect::<BTreeSet<_>>();
        assert_eq!(USER_OWNED_RELATIONS.len(), registered.len());
        assert_eq!(registered, discovered);
    }
}
