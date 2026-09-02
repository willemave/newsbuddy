use std::collections::HashSet;

use newsly_db::{AgentKnowledgeItem, AgentLibraryRepositoryError, find_agent_knowledge_items};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use sqlx::PgPool;
use thiserror::Error;

use crate::content_body_store::{ContentBodyStore, ContentBodyStoreError};

const DEFAULT_KNOWLEDGE_READ_BYTES: usize = 100_000;
const MAX_KNOWLEDGE_READ_BYTES: usize = 500_000;

#[derive(Debug, Clone, Deserialize, JsonSchema, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum KnowledgeReferenceInput {
    Content { id: i64 },
}

impl KnowledgeReferenceInput {
    pub(crate) const fn id(&self) -> i64 {
        match self {
            Self::Content { id } => *id,
        }
    }
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(crate) struct ReadKnowledgeInput {
    pub(crate) reference: KnowledgeReferenceInput,
    pub(crate) offset: Option<usize>,
    pub(crate) max_bytes: Option<usize>,
}

#[derive(Debug, Serialize)]
pub(crate) struct KnowledgeReadOutput {
    pub(crate) reference: KnowledgeReferenceInput,
    pub(crate) title: String,
    pub(crate) text: String,
    pub(crate) checksum_sha256: String,
    pub(crate) offset: usize,
    pub(crate) end_offset: usize,
    pub(crate) truncated: bool,
    pub(crate) next_offset: Option<usize>,
}

pub(crate) async fn read_authorized_knowledge_item(
    pool: &PgPool,
    user_id: i64,
    body_store: &ContentBodyStore,
    input: ReadKnowledgeInput,
) -> Result<KnowledgeReadOutput, KnowledgeToolError> {
    let item = authorized_knowledge_items(pool, user_id, &[input.reference])
        .await?
        .into_iter()
        .next()
        .ok_or(KnowledgeToolError::Unauthorized)?;
    let body = knowledge_body(body_store, &item).await?;
    let offset = input.offset.unwrap_or(0);
    if offset > body.len() || !body.is_char_boundary(offset) {
        return Err(KnowledgeToolError::InvalidOffset);
    }
    let maximum = input
        .max_bytes
        .unwrap_or(DEFAULT_KNOWLEDGE_READ_BYTES)
        .clamp(1, MAX_KNOWLEDGE_READ_BYTES);
    let end = utf8_prefix_end(&body, offset, maximum);
    let truncated = end < body.len();
    Ok(KnowledgeReadOutput {
        reference: KnowledgeReferenceInput::Content {
            id: item.content_id,
        },
        title: item.title,
        text: body[offset..end].to_owned(),
        checksum_sha256: sha256_hex(body.as_bytes()),
        offset,
        end_offset: end,
        truncated,
        next_offset: truncated.then_some(end),
    })
}

pub(crate) async fn authorized_knowledge_items(
    pool: &PgPool,
    user_id: i64,
    references: &[KnowledgeReferenceInput],
) -> Result<Vec<AgentKnowledgeItem>, KnowledgeToolError> {
    if references.is_empty()
        || references.len() > 20
        || references.iter().any(|reference| reference.id() <= 0)
    {
        return Err(KnowledgeToolError::InvalidReferences);
    }
    let ids = references
        .iter()
        .map(KnowledgeReferenceInput::id)
        .collect::<Vec<_>>();
    if ids.iter().copied().collect::<HashSet<_>>().len() != ids.len() {
        return Err(KnowledgeToolError::DuplicateReferences);
    }
    let items = find_agent_knowledge_items(pool, user_id, &ids).await?;
    if items.len() != ids.len() {
        return Err(KnowledgeToolError::Unauthorized);
    }
    Ok(items)
}

pub(crate) async fn knowledge_body(
    body_store: &ContentBodyStore,
    item: &AgentKnowledgeItem,
) -> Result<String, KnowledgeToolError> {
    if let Some(key) = item.storage_key.as_deref() {
        let bytes = body_store
            .get_bytes(key)
            .await?
            .ok_or(KnowledgeToolError::MissingCanonicalBody)?;
        return String::from_utf8(bytes).map_err(|_| KnowledgeToolError::InvalidUtf8);
    }
    item.fallback_text
        .clone()
        .ok_or(KnowledgeToolError::MissingBody)
}

pub(crate) async fn knowledge_body_prefix(
    body_store: &ContentBodyStore,
    item: &AgentKnowledgeItem,
    maximum: usize,
) -> Result<(String, bool), KnowledgeToolError> {
    if let Some(key) = item.storage_key.as_deref() {
        let slice = body_store
            .get_bytes_up_to(key, maximum.saturating_add(4))
            .await?
            .ok_or(KnowledgeToolError::MissingCanonicalBody)?;
        let valid = match std::str::from_utf8(&slice.bytes) {
            Ok(valid) => valid,
            Err(error) if error.error_len().is_none() => {
                std::str::from_utf8(&slice.bytes[..error.valid_up_to()])
                    .map_err(|_| KnowledgeToolError::InvalidUtf8)?
            }
            Err(_) => {
                return Err(KnowledgeToolError::InvalidUtf8);
            }
        };
        let end = utf8_prefix_end(valid, 0, maximum);
        return Ok((
            valid[..end].to_owned(),
            slice.truncated || end < valid.len(),
        ));
    }
    let body = item
        .fallback_text
        .as_deref()
        .ok_or(KnowledgeToolError::MissingBody)?;
    let end = utf8_prefix_end(body, 0, maximum);
    Ok((body[..end].to_owned(), end < body.len()))
}

#[derive(Debug, Error)]
pub(crate) enum KnowledgeToolError {
    #[error("Knowledge references must use kind=content and a positive id")]
    InvalidReferences,
    #[error("Knowledge references must not contain duplicate ids")]
    DuplicateReferences,
    #[error("one or more Knowledge references are not authorized")]
    Unauthorized,
    #[error("offset must be a UTF-8 byte boundary within the Knowledge item")]
    InvalidOffset,
    #[error("Knowledge item's canonical body object is missing")]
    MissingCanonicalBody,
    #[error("Knowledge item has no readable body")]
    MissingBody,
    #[error("Knowledge body is not valid UTF-8")]
    InvalidUtf8,
    #[error(transparent)]
    Repository(#[from] AgentLibraryRepositoryError),
    #[error(transparent)]
    Storage(#[from] ContentBodyStoreError),
}

pub(crate) fn utf8_prefix_end(value: &str, offset: usize, maximum: usize) -> usize {
    let mut end = offset.saturating_add(maximum).min(value.len());
    while end > offset && !value.is_char_boundary(end) {
        end -= 1;
    }
    end
}

pub(crate) fn sha256_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let digest = Sha256::digest(bytes);
    let mut encoded = String::with_capacity(digest.len() * 2);
    for byte in digest {
        encoded.push(char::from(HEX[usize::from(byte >> 4)]));
        encoded.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    encoded
}

#[cfg(test)]
mod tests {
    use super::{KnowledgeReferenceInput, sha256_hex, utf8_prefix_end};

    #[test]
    fn knowledge_reference_is_a_closed_tagged_type() {
        let reference: KnowledgeReferenceInput =
            serde_json::from_value(serde_json::json!({"kind": "content", "id": 42})).unwrap();
        assert_eq!(reference.id(), 42);
        assert!(
            serde_json::from_value::<KnowledgeReferenceInput>(
                serde_json::json!({"kind": "document", "id": 42})
            )
            .is_err()
        );
        assert!(
            serde_json::from_value::<KnowledgeReferenceInput>(
                serde_json::json!({"kind": "content", "id": 42, "extra": true})
            )
            .is_err()
        );
    }

    #[test]
    fn bounded_knowledge_text_never_splits_utf8() {
        let value = "abécd";
        assert_eq!(utf8_prefix_end(value, 0, 3), 2);
        assert_eq!(utf8_prefix_end(value, 2, 2), 4);
        assert_eq!(utf8_prefix_end(value, 4, 50), value.len());
    }

    #[test]
    fn knowledge_checksum_is_stable() {
        assert_eq!(
            sha256_hex(b"knowledge"),
            "e0f895872d65b2528feec97350a3a212b3d4ab88748e25d022a34641d338216b"
        );
    }
}
