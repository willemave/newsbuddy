use super::actions::{ResolvedSource, StateChange};
use super::catalog::HostTool;
use super::{
    AgentRuntimeError, AggregatorKey, ChatToolExecutor, Deserialize, JsonSchema, Serialize,
    ToolCall, ToolOutput, Value, aggregator_feed_option, arguments, assistant_feed_option,
    bounded_query, canonical_direct_feed_url, collected_activity, db_tool_error, json,
    load_active_feed_urls, record_exa_tool_usage, success, valid_url,
};
use newsly_providers::{SourceKind, SourceResolutionStatus};
#[path = "content.rs"]
mod content;

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Deserialize, Serialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub(super) enum Reference {
    Content {
        id: i64,
    },
    #[serde(rename = "news_item")]
    News {
        id: i64,
    },
}
impl Reference {
    fn id(&self) -> i64 {
        match self {
            Self::Content { id } | Self::News { id } => *id,
        }
    }
}
#[derive(Debug, Clone, Deserialize, Serialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum SourceRef {
    Feed {
        url: String,
        feed_kind: Option<SourceKind>,
    },
    Aggregator {
        key: String,
    },
    Subscription {
        id: i64,
    },
}
#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct Search {
    scope: Scope,
    query: Option<String>,
    source: Option<SourceRef>,
    unread_only: Option<bool>,
    limit: Option<i64>,
    offset: Option<i64>,
}
#[derive(Debug, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
enum Scope {
    Knowledge,
    Content,
    News,
}
#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct Read {
    reference: Reference,
    offset: Option<usize>,
    max_bytes: Option<usize>,
}
#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct ReadState {
    references: Vec<Reference>,
    is_read: bool,
}
#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct SavedState {
    references: Vec<Reference>,
    is_saved: bool,
}
#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct Sources {
    query: Option<String>,
    kind: Option<SourceKind>,
    active_only: Option<bool>,
    limit: Option<i64>,
    offset: Option<i64>,
}
#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct Subscribe {
    source: SourceRef,
    title: Option<String>,
    /// Aggregator filter only: omit to preserve, [] to follow every topic.
    topics: Option<Vec<String>>,
}
#[derive(Debug, Deserialize, JsonSchema)]
#[serde(untagged)]
enum InboxTarget {
    Url(String),
    Reference(Reference),
}
#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct Inbox {
    target: InboxTarget,
    title: Option<String>,
}
#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct Resolve {
    input: String,
    kind: Option<SourceKind>,
    hosts: Option<Vec<String>>,
}
#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct WebPage {
    url: String,
    max_chars: Option<usize>,
}

impl ChatToolExecutor {
    async fn source(&self, source: SourceRef) -> Result<ResolvedSource, AgentRuntimeError> {
        match source {
            SourceRef::Aggregator { key } => AggregatorKey::from_key(&key)
                .map(ResolvedSource::Aggregator)
                .ok_or_else(|| db_tool_error("Unknown aggregator")),
            SourceRef::Feed { url, feed_kind } => {
                if feed_kind == Some(SourceKind::Aggregator) {
                    return Err(db_tool_error(
                        "Use an aggregator reference for an aggregator",
                    ));
                }
                Ok(ResolvedSource::Feed {
                    url: valid_url(&url)?,
                    title: None,
                    kind: feed_kind,
                    reference: None,
                })
            }
            SourceRef::Subscription { id } => {
                let source =
                    newsly_db::find_chat_source(&self.dependencies.pool, self.snapshot.user_id, id)
                        .await
                        .map_err(db_tool_error)?
                        .ok_or_else(|| {
                            db_tool_error(
                                "Source reference is not owned by this user or unavailable",
                            )
                        })?;
                let url = source
                    .feed_url
                    .ok_or_else(|| db_tool_error("Source has no exact feed URL"))?;
                if let Some(key) = url.strip_prefix("aggregator://") {
                    AggregatorKey::from_key(key)
                        .map(ResolvedSource::Aggregator)
                        .ok_or_else(|| db_tool_error("Unknown aggregator"))
                } else {
                    Ok(ResolvedSource::Feed {
                        url: valid_url(&url)?,
                        title: Some(source.title),
                        kind: Some(if source.kind == "podcast" {
                            SourceKind::Podcast
                        } else {
                            SourceKind::Publication
                        }),
                        reference: Some(newsly_db::FeedSubscriptionReference { id, feed_url: url }),
                    })
                }
            }
        }
    }
    pub(super) async fn execute_canonical(
        &self,
        tool: HostTool,
        call: ToolCall,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        match tool {
            HostTool::SearchWeb => self.search_web(call).await,
            HostTool::ReadWebPage => {
                let input: WebPage = arguments(&call)?;
                let page = newsly_providers::read_public_web_page(
                    &input.url,
                    input.max_chars.unwrap_or(12000),
                )
                .await
                .map_err(db_tool_error)?;
                Ok(success(serde_json::to_value(page).map_err(db_tool_error)?))
            }
            HostTool::ResolveSource => self.resolve_source(call).await,
            HostTool::ListSources => {
                let input: Sources = arguments(&call)?;
                let limit = input.limit.unwrap_or(20);
                let offset = input.offset.unwrap_or(0);
                if !(1..=25).contains(&limit) {
                    return Err(db_tool_error("limit must be 1-25"));
                }
                let rows = newsly_db::list_chat_sources(
                    &self.dependencies.pool,
                    self.snapshot.user_id,
                    &input.query.unwrap_or_default(),
                    input.kind.map(SourceKind::as_str),
                    input.active_only.unwrap_or(true),
                    limit + 1,
                    offset,
                )
                .await
                .map_err(db_tool_error)?;
                let has_more = rows.len() > usize::try_from(limit).unwrap_or(0);
                let keys = rows
                    .iter()
                    .filter_map(|row| {
                        row.feed_url
                            .as_deref()
                            .and_then(|url| url.strip_prefix("aggregator://"))
                            .and_then(AggregatorKey::from_key)
                    })
                    .collect::<Vec<_>>();
                let checked = chrono::Utc::now();
                let activity = if keys.is_empty() {
                    Vec::new()
                } else {
                    newsly_db::aggregator_corpus::load_aggregator_collected_activity(
                        &self.dependencies.pool,
                        &keys,
                        checked,
                    )
                    .await
                    .unwrap_or_default()
                };
                let activity = activity
                    .into_iter()
                    .map(|row| {
                        (
                            format!("aggregator://{}", row.key),
                            collected_activity(row, checked),
                        )
                    })
                    .collect::<std::collections::BTreeMap<_, _>>();
                let sources=rows.into_iter().take(usize::try_from(limit).unwrap_or(0)).map(|row| {
                    let publishing_activity=row.feed_url.as_ref().and_then(|url|activity.get(url));
                    json!({"reference":{"kind":"subscription","id":row.id},"title":row.title,"kind":row.kind,"feed_url":row.feed_url,"is_subscribed":row.is_active,"publishing_activity":publishing_activity})
                }).collect::<Vec<_>>();
                Ok(success(
                    json!({"sources":sources,"has_more":has_more,"next_offset":has_more.then_some(offset+limit)}),
                ))
            }
            HostTool::SearchContent => self.canonical_search(call).await,
            HostTool::ReadContent => self.canonical_read(call).await,
            HostTool::SetReadState => {
                let input: ReadState = arguments(&call)?;
                self.set_state(input.references, StateChange::Read(input.is_read))
                    .await
            }
            HostTool::SetSavedState => {
                let input: SavedState = arguments(&call)?;
                self.set_state(input.references, StateChange::Saved(input.is_saved))
                    .await
            }
            HostTool::SubscribeToSource => {
                let input: Subscribe = arguments(&call)?;
                match self.source(input.source).await? {
                    ResolvedSource::Aggregator(key) => {
                        self.subscribe_aggregator(key.as_str(), input.topics).await
                    }
                    source @ ResolvedSource::Feed { .. } => {
                        if input.topics.is_some() {
                            return Err(db_tool_error("topics applies only to aggregator sources"));
                        }
                        self.subscribe(source, input.title).await
                    }
                }
            }
            HostTool::AddToInbox => {
                let input: Inbox = arguments(&call)?;
                let url = match input.target {
                    InboxTarget::Url(url) => url,
                    InboxTarget::Reference(Reference::News { id }) => {
                        let item = newsly_db::read_chat_news(
                            &self.dependencies.pool,
                            self.snapshot.user_id,
                            id,
                        )
                        .await
                        .map_err(db_tool_error)?
                        .ok_or_else(|| db_tool_error("News reference is not authorized"))?;
                        item.get("article_url")
                            .and_then(Value::as_str)
                            .or_else(|| item.get("story_url").and_then(Value::as_str))
                            .ok_or_else(|| db_tool_error("News item has no article URL"))?
                            .to_owned()
                    }
                    InboxTarget::Reference(Reference::Content { id }) => {
                        newsly_db::find_agent_readable_items(
                            &self.dependencies.pool,
                            self.snapshot.user_id,
                            &[id],
                            false,
                        )
                        .await
                        .map_err(db_tool_error)?
                        .into_iter()
                        .next()
                        .ok_or_else(|| db_tool_error("Content reference is not authorized"))?
                        .url
                    }
                };
                self.submit_content(&url, input.title).await
            }
        }
    }
    async fn resolve_source(&self, call: ToolCall) -> Result<ToolOutput, AgentRuntimeError> {
        let input: Resolve = arguments(&call)?;
        let query = bounded_query(&input.input)?;
        if query.len() > 2048
            || input.hosts.as_ref().is_some_and(|hosts| {
                hosts.len() > 8
                    || hosts
                        .iter()
                        .any(|host| host.trim().is_empty() || host.len() > 200)
            })
        {
            return Err(db_tool_error(
                "Use input up to 2048 bytes and at most 8 nonempty hosts up to 200 bytes each",
            ));
        }
        let keys = if matches!(
            input.kind,
            Some(SourceKind::Podcast | SourceKind::Publication)
        ) {
            Vec::new()
        } else {
            AggregatorKey::ALL
                .into_iter()
                .filter(|key| {
                    query.eq_ignore_ascii_case(key.as_str())
                        || query.eq_ignore_ascii_case(key.display_name())
                        || query.eq_ignore_ascii_case(&format!("aggregator://{}", key.as_str()))
                })
                .collect()
        };
        let mut options = keys
            .iter()
            .copied()
            .map(aggregator_feed_option)
            .collect::<Vec<_>>();
        let mut limitations = Vec::new();
        let mut status = SourceResolutionStatus::Found;
        if keys.is_empty() {
            let resolution = self
                .dependencies
                .source_resolver
                .resolve_source(query, input.kind, &input.hosts.unwrap_or_default())
                .await
                .map_err(db_tool_error)?;
            status = resolution.status;
            limitations = resolution.limitations;
            record_exa_tool_usage(
                &self.dependencies.pool,
                self.snapshot.user_id,
                &call.id,
                "chat_tool.source_resolution",
                resolution.exa_usage.into(),
            )
            .await;
            options.extend(resolution.feeds.into_iter().map(assistant_feed_option));
        }
        let active = load_active_feed_urls(&self.dependencies.pool, self.snapshot.user_id)
            .await
            .map_err(db_tool_error)?
            .into_iter()
            .map(|url| canonical_direct_feed_url(&url))
            .collect::<std::collections::BTreeSet<_>>();
        if !keys.is_empty() {
            let checked = chrono::Utc::now();
            match newsly_db::aggregator_corpus::load_aggregator_collected_activity(
                &self.dependencies.pool,
                &keys,
                checked,
            )
            .await
            {
                Ok(rows) => {
                    for row in rows {
                        if let Some(option) = options
                            .iter_mut()
                            .find(|option| option.feed_url == format!("aggregator://{}", row.key))
                        {
                            option.publishing_activity = Some(collected_activity(row, checked));
                        }
                    }
                }
                Err(_) => limitations.push("Aggregator collected activity is unavailable".into()),
            }
        }
        for option in &mut options {
            option.is_subscribed = active.contains(&canonical_direct_feed_url(&option.feed_url));
        }
        self.attach_feed_options(&options);
        let sources=options.iter().map(|option|json!({"reference":if let Some(key)=option.feed_url.strip_prefix("aggregator://"){json!({"kind":"aggregator","key":key})}else{json!({"kind":"feed","url":option.feed_url,"feed_kind":if option.feed_type == newsly_contracts::FeedType::PodcastRss {SourceKind::Podcast}else{SourceKind::Publication}})},"option":option})).collect::<Vec<_>>();
        Ok(success(
            json!({"status":status,"sources":sources,"options":options,"limitations":limitations}),
        ))
    }
}

#[cfg(test)]
#[path = "canonical_tests.rs"]
mod tests;
