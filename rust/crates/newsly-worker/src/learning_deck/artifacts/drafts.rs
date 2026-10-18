//! Private, immutable candidates. A draft is never a public artifact or proof of validation.
use super::*;
use serde::{Deserialize, Serialize};
use serde_json::Map;
use sha2::{Digest as _, Sha256};

const DRAFT_VERSION: u32 = 1;
const MAX_DRAFT_BYTES: usize = 20 * 1024 * 1024;

#[derive(Debug, Clone)]
pub(in crate::learning_deck) struct DeckCandidate {
    pub index_html: String,
    pub source_notes: String,
    pub source_metadata: Map<String, Value>,
    pub assets: Vec<LearningDeckAsset>,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct DraftManifest {
    version: u32,
    fingerprint: String,
    index_html: String,
    source_notes: String,
    source_metadata: Map<String, Value>,
    assets: Vec<DraftAsset>,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct DraftAsset {
    relative_path: String,
    digest: String,
    bytes: usize,
}

pub(in crate::learning_deck) fn digest(bytes: &[u8]) -> String {
    use std::fmt::Write as _;
    Sha256::digest(bytes)
        .iter()
        .fold(String::with_capacity(64), |mut hex, byte| {
            write!(hex, "{byte:02x}").expect("writing into a String cannot fail");
            hex
        })
}

impl DeckCandidate {
    pub(in crate::learning_deck) fn check_bounds(
        &self,
        limits: &LearningDeckArtifactLimits,
    ) -> Result<(), LearningDeckArtifactError> {
        if self.index_html.len() > limits.index_html_bytes
            || self.source_notes.len() > limits.source_notes_bytes
            || self.assets.len() > limits.asset_count
        {
            return Err(LearningDeckArtifactError::Contract(
                "draft exceeds artifact bounds".to_owned(),
            ));
        }
        let mut paths = BTreeSet::new();
        let mut total = self
            .index_html
            .len()
            .saturating_add(self.source_notes.len());
        for asset in &self.assets {
            if normalize_asset_path(&asset.relative_path)? != asset.relative_path
                || !paths.insert(&asset.relative_path)
                || asset.bytes.len() > limits.asset_bytes
            {
                return Err(LearningDeckArtifactError::Contract(
                    "invalid draft asset".to_owned(),
                ));
            }
            total = total.saturating_add(asset.bytes.len());
        }
        if total > MAX_DRAFT_BYTES {
            return Err(LearningDeckArtifactError::Contract(
                "draft exceeds the 20 MiB aggregate limit".to_owned(),
            ));
        }
        Ok(())
    }
}

impl LearningDeckArtifactStore {
    pub(in crate::learning_deck) async fn store_draft(
        &self,
        user_id: i64,
        deck_id: i64,
        task_id: i64,
        fingerprint: &str,
        candidate: &DeckCandidate,
    ) -> Result<String, LearningDeckArtifactError> {
        candidate.check_bounds(&self.limits)?;
        let prefix = join_key(
            &self.prefix,
            &format!(
                "learning_deck_drafts/{user_id}/{deck_id}/{task_id}/{}",
                Uuid::new_v4()
            ),
        )?;
        let mut assets = Vec::new();
        for asset in &candidate.assets {
            let digest = digest(&asset.bytes);
            let key = join_key(&prefix, &digest)?;
            newsly_db::track_artifact(&self.pool, &key, task_id).await?;
            self.put_bytes(&key, asset.bytes.clone(), "application/octet-stream")
                .await?;
            assets.push(DraftAsset {
                relative_path: asset.relative_path.clone(),
                digest,
                bytes: asset.bytes.len(),
            });
        }
        let manifest = DraftManifest {
            version: DRAFT_VERSION,
            fingerprint: fingerprint.to_owned(),
            index_html: candidate.index_html.clone(),
            source_notes: candidate.source_notes.clone(),
            source_metadata: candidate.source_metadata.clone(),
            assets,
        };
        let bytes = serde_json::to_vec(&manifest)?;
        if bytes.len() > MAX_DRAFT_BYTES {
            return Err(LearningDeckArtifactError::Contract(
                "draft manifest exceeds limit".to_owned(),
            ));
        }
        let key = join_key(&prefix, "draft.json")?;
        newsly_db::track_artifact(&self.pool, &key, task_id).await?;
        self.put_bytes(&key, bytes, "application/json").await?;
        Ok(key)
    }

    pub(in crate::learning_deck) async fn load_draft(
        &self,
        user_id: i64,
        deck_id: i64,
        task_id: i64,
        fingerprint: &str,
        key: &str,
    ) -> Result<Option<DeckCandidate>, LearningDeckArtifactError> {
        let prefix = self.draft_prefix(user_id, deck_id, task_id, key)?;
        let Some(raw) = self.get_text(key).await? else {
            return Ok(None);
        };
        let manifest: DraftManifest = serde_json::from_str(&raw)?;
        if manifest.version != DRAFT_VERSION
            || manifest.fingerprint != fingerprint
            || manifest.assets.len() > self.limits.asset_count
        {
            return Err(LearningDeckArtifactError::Contract(
                "draft identity or version mismatch".to_owned(),
            ));
        }
        let mut assets = Vec::new();
        let mut total = manifest
            .index_html
            .len()
            .saturating_add(manifest.source_notes.len());
        for asset in manifest.assets {
            if asset.digest.len() != 64
                || !asset.digest.bytes().all(|ch| ch.is_ascii_hexdigit())
                || asset.bytes > self.limits.asset_bytes
            {
                return Err(LearningDeckArtifactError::Contract(
                    "invalid draft asset digest or size".to_owned(),
                ));
            }
            total = total.saturating_add(asset.bytes);
            if total > MAX_DRAFT_BYTES {
                return Err(LearningDeckArtifactError::Contract(
                    "draft exceeds aggregate limit".to_owned(),
                ));
            }
            let Some(bytes) = self.get_bytes(&join_key(&prefix, &asset.digest)?).await? else {
                return Ok(None);
            };
            if bytes.len() != asset.bytes || digest(&bytes) != asset.digest {
                return Err(LearningDeckArtifactError::Contract(
                    "draft asset checksum mismatch".to_owned(),
                ));
            }
            assets.push(LearningDeckAsset {
                content_type: guess_content_type(&asset.relative_path),
                relative_path: asset.relative_path,
                bytes,
            });
        }
        let candidate = DeckCandidate {
            index_html: manifest.index_html,
            source_notes: manifest.source_notes,
            source_metadata: manifest.source_metadata,
            assets,
        };
        candidate.check_bounds(&self.limits)?;
        Ok(Some(candidate))
    }

    fn draft_prefix(
        &self,
        user_id: i64,
        deck_id: i64,
        task_id: i64,
        key: &str,
    ) -> Result<String, LearningDeckArtifactError> {
        validate_object_key(key)?;
        let root = join_key(
            &self.prefix,
            &format!("learning_deck_drafts/{user_id}/{deck_id}/{task_id}/"),
        )?;
        let suffix = key
            .strip_prefix(&format!("{}/", root.trim_end_matches('/')))
            .and_then(|rest| rest.strip_suffix("/draft.json"));
        if suffix.is_none_or(|id| Uuid::parse_str(id).is_err()) {
            return Err(LearningDeckArtifactError::Contract(
                "draft belongs to a different task".to_owned(),
            ));
        }
        Ok(key.trim_end_matches("/draft.json").to_owned())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    async fn fixture(
        pool: sqlx::PgPool,
        prefix: &str,
    ) -> (tempfile::TempDir, LearningDeckArtifactStore) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let root = tempfile::tempdir().unwrap();
        let store = LearningDeckArtifactStore {
            pool,
            backend: ArtifactBackend::Local {
                root: root.path().to_owned(),
            },
            prefix: prefix.to_owned(),
            limits: LearningDeckArtifactLimits {
                index_html_bytes: 10_000,
                source_notes_bytes: 10_000,
                asset_count: 10,
                asset_bytes: 10_000,
            },
        };
        (root, store)
    }

    fn candidate() -> DeckCandidate {
        DeckCandidate {
            index_html: "<html><section>Private unfinished candidate</section></html>".to_owned(),
            source_notes: "# Evidence\nSource notes preserved verbatim.".to_owned(),
            source_metadata: serde_json::from_value(
                json!({"source_id":42,"nested":{"title":"Evidence"}}),
            )
            .unwrap(),
            assets: vec![LearningDeckAsset {
                relative_path: "assets/diagrams/figure.svg".to_owned(),
                bytes: b"<svg>fixture</svg>".to_vec(),
                content_type: "image/svg+xml".to_owned(),
            }],
        }
    }

    #[sqlx::test]
    async fn private_checkpoint_round_trip_preserves_candidate_and_tracks_objects(
        pool: sqlx::PgPool,
    ) {
        let (_root, store) = fixture(pool, "content").await;
        let original = candidate();
        let key = store
            .store_draft(1, 2, 3, "input-fingerprint", &original)
            .await
            .unwrap();
        assert!(key.starts_with("content/learning_deck_drafts/1/2/3/"));
        assert!(key.ends_with("/draft.json"));
        let restored = store
            .load_draft(1, 2, 3, "input-fingerprint", &key)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(restored.index_html, original.index_html);
        assert_eq!(restored.source_notes, original.source_notes);
        assert_eq!(restored.source_metadata, original.source_metadata);
        assert_eq!(restored.assets.len(), 1);
        assert_eq!(
            restored.assets[0].relative_path,
            original.assets[0].relative_path
        );
        assert_eq!(restored.assets[0].bytes, original.assets[0].bytes);
        assert_eq!(
            restored.assets[0].content_type,
            original.assets[0].content_type
        );
        let tracked: Vec<String> = sqlx::query_scalar("SELECT object_key FROM artifact_cleanup_candidates WHERE llm_task_id = $1 ORDER BY object_key")
            .bind(3_i64).fetch_all(&store.pool).await.unwrap();
        assert_eq!(tracked.len(), 2);
        assert!(tracked.contains(&key));
        assert!(
            tracked
                .iter()
                .any(|key| key.ends_with(&digest(&original.assets[0].bytes)))
        );
        let second_key = store
            .store_draft(1, 2, 3, "input-fingerprint", &original)
            .await
            .unwrap();
        assert_ne!(
            key, second_key,
            "retry snapshots must be immutable, isolated objects"
        );
    }

    #[sqlx::test]
    async fn corrupt_checkpoint_asset_never_restores_a_candidate(pool: sqlx::PgPool) {
        let (root, store) = fixture(pool, "").await;
        let original = candidate();
        let key = store
            .store_draft(1, 2, 3, "fingerprint", &original)
            .await
            .unwrap();
        let prefix = store.draft_prefix(1, 2, 3, &key).unwrap();
        let asset_key = join_key(&prefix, &digest(&original.assets[0].bytes)).unwrap();
        // Preserve the length so only digest verification can detect this corruption.
        let mut corrupt = original.assets[0].bytes.clone();
        corrupt[0] ^= 1;
        tokio::fs::write(root.path().join(&asset_key), corrupt)
            .await
            .unwrap();
        let error = store
            .load_draft(1, 2, 3, "fingerprint", &key)
            .await
            .unwrap_err();
        assert!(
            matches!(error, LearningDeckArtifactError::Contract(ref reason) if reason.contains("checksum"))
        );
    }

    #[sqlx::test]
    async fn missing_checkpoint_objects_return_none_without_partial_candidate(pool: sqlx::PgPool) {
        let (root, store) = fixture(pool, "content").await;
        let original = candidate();
        let key = store
            .store_draft(1, 2, 3, "fingerprint", &original)
            .await
            .unwrap();
        let prefix = store.draft_prefix(1, 2, 3, &key).unwrap();
        let asset_key = join_key(&prefix, &digest(&original.assets[0].bytes)).unwrap();
        tokio::fs::remove_file(root.path().join(asset_key))
            .await
            .unwrap();
        assert!(
            store
                .load_draft(1, 2, 3, "fingerprint", &key)
                .await
                .unwrap()
                .is_none()
        );
        tokio::fs::remove_file(root.path().join(&key))
            .await
            .unwrap();
        assert!(
            store
                .load_draft(1, 2, 3, "fingerprint", &key)
                .await
                .unwrap()
                .is_none()
        );
    }

    #[sqlx::test]
    async fn checkpoint_identity_rejects_other_jobs_fingerprints_and_prefixes(pool: sqlx::PgPool) {
        let (_root, store) = fixture(pool, "content").await;
        let key = store
            .store_draft(1, 2, 3, "fingerprint", &candidate())
            .await
            .unwrap();
        for (user, deck, task) in [(9, 2, 3), (1, 9, 3), (1, 2, 9)] {
            assert!(
                store
                    .load_draft(user, deck, task, "fingerprint", &key)
                    .await
                    .is_err()
            );
        }
        assert!(
            store
                .load_draft(1, 2, 3, "other-fingerprint", &key)
                .await
                .is_err()
        );
        for invalid in [
            key.replace("content/", "another/"),
            key.replace("draft.json", "index.html"),
            "content/learning_deck_drafts/1/2/3/not-a-uuid/draft.json".to_owned(),
        ] {
            assert!(store.draft_prefix(1, 2, 3, &invalid).is_err());
        }
        assert_eq!(
            store.draft_prefix(1, 2, 3, &key).unwrap(),
            key.strip_suffix("/draft.json").unwrap()
        );
    }
}
