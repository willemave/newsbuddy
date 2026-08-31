//! Pure Newsly domain types. This crate has no database or network dependencies.

#![forbid(unsafe_code)]

mod ids;
mod news_relations;
mod ownership;

pub use ids::{
    BriefingVersion, ChatSessionId, ContentId, InvalidDatabaseId, InvalidGeneration, LeaseToken,
    LlmTaskId, NewsItemId, StreamGeneration, UserId,
};
pub use news_relations::{
    EmbeddingVector, EmbeddingVectorStore, InvalidEmbeddingBundle, NewsRelationDocument,
    RelationDecision, RelationDecisionOutcome, RelationEmbeddingText, RelationExactKey,
    RelationMatchError, RelationMatchPath, RelationMatchResult, RelationThresholds,
    aggregate_relation_representative, can_bridge_relation_clusters,
    prepare_relation_embedding_texts, related_representatives, relation_embedding_key,
};
pub use ownership::{
    ApplicationSha, InvalidOwnershipValue, OwnershipRecord, OwnershipTarget, OwnershipVersion,
    ReadinessState, ReplicaId, ResourceKey, ResourceKind, RuntimeOwner, TransitionIntent,
    TransitionState,
};
