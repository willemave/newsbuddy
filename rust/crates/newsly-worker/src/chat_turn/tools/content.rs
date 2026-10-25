use super::super::actions::{ConfirmedAction, ResolvedSource, StateChange};
use super::super::{
    BTreeSet, ChatContentFilters, mark_content_read, mark_content_unread,
    remove_content_from_knowledge, save_content_to_knowledge, search_accessible_content,
};
use super::{
    AgentRuntimeError, ChatToolExecutor, Read, Reference, Scope, Search, ToolCall, ToolOutput,
    arguments, db_tool_error, json, success,
};
use crate::knowledge_tools::{knowledge_body, utf8_prefix_end};
impl ChatToolExecutor {
    pub(super) async fn canonical_search(
        &self,
        call: ToolCall,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        let input: Search = arguments(&call)?;
        let limit = input.limit.unwrap_or(20);
        let offset = input.offset.unwrap_or(0);
        if !(1..=25).contains(&limit) || !(0..=100_000).contains(&offset) {
            return Err(db_tool_error("limit must be 1-25 and offset 0-100_000"));
        }
        let source = match input.source {
            Some(source) => Some(self.source(source).await?),
            None => None,
        };
        let query = input.query.unwrap_or_default();
        if matches!(input.scope, Scope::News) {
            let key = match source {
                Some(ResolvedSource::Aggregator(key)) => Some(key),
                Some(ResolvedSource::Feed { .. }) => {
                    return Err(db_tool_error("News source must be an aggregator reference"));
                }
                None => None,
            };
            return Ok(success(
                newsly_db::search_chat_news_page(
                    &self.dependencies.pool,
                    self.snapshot.user_id,
                    &query,
                    input.unread_only.unwrap_or(false),
                    key.map(newsly_domain::AggregatorKey::as_str),
                    limit,
                    offset,
                )
                .await
                .map_err(db_tool_error)?,
            ));
        }
        let (name, url) = match source {
            Some(ResolvedSource::Feed { url, title, .. }) => (title, Some(url)),
            Some(ResolvedSource::Aggregator(_)) => {
                return Err(db_tool_error(
                    "Aggregator sources belong to scope=news; use a podcast/publication source for knowledge or content",
                ));
            }
            None => (None, None),
        };
        let filters = ChatContentFilters {
            query,
            source: name,
            source_url: url,
            unread_only: input.unread_only.unwrap_or(false),
            saved_only: matches!(input.scope, Scope::Knowledge),
            limit,
            offset,
        };
        let page =
            search_accessible_content(&self.dependencies.pool, self.snapshot.user_id, &filters)
                .await
                .map_err(db_tool_error)?;
        Ok(success(serde_json::to_value(page).map_err(db_tool_error)?))
    }
    pub(super) async fn canonical_read(
        &self,
        call: ToolCall,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        let input: Read = arguments(&call)?;
        if input.reference.id() <= 0 {
            return Err(db_tool_error("Reference id must be positive"));
        }
        match input.reference {
            Reference::News { id } => Ok(success(
                newsly_db::read_chat_news(&self.dependencies.pool, self.snapshot.user_id, id)
                    .await
                    .map_err(db_tool_error)?
                    .ok_or_else(|| db_tool_error("News reference is not authorized"))?,
            )),
            Reference::Content { id } => {
                let item = newsly_db::find_agent_readable_items(
                    &self.dependencies.pool,
                    self.snapshot.user_id,
                    &[id],
                    false,
                )
                .await
                .map_err(db_tool_error)?
                .into_iter()
                .next()
                .ok_or_else(|| db_tool_error("Content reference is not authorized"))?;
                let body = knowledge_body(&self.dependencies.body_store, &item)
                    .await
                    .map_err(db_tool_error)?;
                let offset = input.offset.unwrap_or(0);
                if offset > body.len() || !body.is_char_boundary(offset) {
                    return Err(db_tool_error(
                        "Offset must be a UTF-8 boundary within the body",
                    ));
                }
                let end = utf8_prefix_end(
                    &body,
                    offset,
                    input.max_bytes.unwrap_or(100_000).clamp(1, 500_000),
                );
                Ok(success(
                    json!({"reference":{"kind":"content","id":id},"title":item.title,"url":item.url,"source":item.source,"text":&body[offset..end],"checksum_sha256":newsly_domain::sha256_hex(body.as_bytes()),"offset":offset,"end_offset":end,"truncated":end<body.len(),"next_offset":(end<body.len()).then_some(end)}),
                ))
            }
        }
    }
    pub(super) async fn set_state(
        &self,
        references: Vec<Reference>,
        change: StateChange,
    ) -> Result<ToolOutput, AgentRuntimeError> {
        let keys = references.iter().collect::<BTreeSet<_>>();
        if references.is_empty()
            || references.len() > 20
            || keys.len() != references.len()
            || references.iter().any(|r| r.id() <= 0)
        {
            return Err(db_tool_error("Use 1-20 distinct positive typed references"));
        }
        if matches!(change, StateChange::Saved(_))
            && references
                .iter()
                .any(|r| matches!(r, Reference::News { .. }))
        {
            return Err(db_tool_error(
                "Save News by adding it to the inbox first, then save the returned content reference",
            ));
        }
        let ids = references
            .iter()
            .filter_map(|r| match r {
                Reference::Content { id } => Some(*id),
                Reference::News { .. } => None,
            })
            .collect::<Vec<_>>();
        let mut tx = self.begin_mutation().await?;
        let authorized = newsly_db::lock_authorized_content(&mut tx, self.snapshot.user_id, &ids)
            .await
            .map_err(db_tool_error)?;
        if authorized.len() != ids.len() {
            return Err(db_tool_error(
                "One or more content references are not authorized",
            ));
        }
        for reference in &references {
            match reference {
                Reference::News { id } => {
                    let StateChange::Read(is_read) = change else {
                        return Err(db_tool_error(
                            "News must be added to the inbox before saving",
                        ));
                    };
                    newsly_db::set_chat_news_read(&mut tx, self.snapshot.user_id, *id, is_read)
                        .await
                        .map_err(db_tool_error)?;
                }
                Reference::Content { id } => match change {
                    StateChange::Read(true) => {
                        mark_content_read(&mut tx, self.snapshot.user_id, *id)
                            .await
                            .map_err(db_tool_error)?;
                    }
                    StateChange::Read(false) => {
                        mark_content_unread(&mut tx, self.snapshot.user_id, *id)
                            .await
                            .map_err(db_tool_error)?;
                    }
                    StateChange::Saved(true) => {
                        save_content_to_knowledge(&mut tx, self.snapshot.user_id, *id)
                            .await
                            .map_err(db_tool_error)?;
                    }
                    StateChange::Saved(false) => {
                        remove_content_from_knowledge(&mut tx, self.snapshot.user_id, *id)
                            .await
                            .map_err(db_tool_error)?;
                    }
                },
            }
        }
        tx.commit().await.map_err(db_tool_error)?;
        for reference in &references {
            self.record_action(ConfirmedAction::StateChanged {
                reference: reference.clone(),
                change,
            });
        }
        let mut response = json!({"references":references,"committed":true});
        match change {
            StateChange::Read(value) => response["is_read"] = json!(value),
            StateChange::Saved(value) => response["is_saved"] = json!(value),
        }
        Ok(success(response))
    }
}
