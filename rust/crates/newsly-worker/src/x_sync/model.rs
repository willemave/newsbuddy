use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use newsly_db::PreparedXSync;
use newsly_providers::XTweet;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct DurableXCredentials {
    pub provider_user_id: Option<String>,
    pub provider_username: Option<String>,
    pub access_token_encrypted: String,
    pub refresh_token_encrypted: Option<String>,
    pub token_expires_at: Option<DateTime<Utc>>,
    pub scopes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub(super) struct XRequestUsage {
    pub model: &'static str,
    pub feature: &'static str,
    pub operation: &'static str,
    pub request_id: String,
    pub request_count: i32,
    pub resource_ids: Vec<String>,
    pub unit_cost_usd: Option<f64>,
    pub channel: Option<&'static str>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct BookmarkFetchOutcome {
    pub status: &'static str,
    pub fetched: usize,
    pub tweets: Vec<XTweet>,
    pub included_tweets: BTreeMap<String, XTweet>,
    pub newest_item_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub(super) enum XSyncMutation {
    Complete {
        credentials: DurableXCredentials,
        bookmarks: BookmarkFetchOutcome,
        usage: Vec<XRequestUsage>,
        completed_at: DateTime<Utc>,
    },
    Failed {
        credentials: Option<DurableXCredentials>,
        usage: Vec<XRequestUsage>,
        error: String,
    },
    ReauthRequired {
        reason: String,
        recorded_at: DateTime<Utc>,
    },
}

#[derive(Debug, Clone, PartialEq)]
pub(super) struct XSyncFinalizationPlan {
    pub task_id: i64,
    pub prepared: PreparedXSync,
    pub mutation: XSyncMutation,
}
