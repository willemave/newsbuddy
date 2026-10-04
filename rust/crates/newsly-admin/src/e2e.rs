//! Local-only iOS end-to-end fixture orchestration and manifest presentation.

use std::collections::BTreeMap;
use std::env;
use std::net::IpAddr;
use std::path::{Component, Path, PathBuf};

use anyhow::{Context, Result, bail};
use newsly_contracts::LlmTaskMode;
use newsly_db::{
    E2eDatabaseIdentity, IosE2eFixtureNamespace, IosE2eFixtureSeedReceipt, IosE2eLocalArtifactPlan,
    inspect_e2e_database_identity, ios_e2e_local_artifact_plan, seed_ios_e2e_fixture,
};
use serde::Serialize;
use serde_json::{Value, json};
use sqlx::PgPool;

const APP_BUNDLE_ID: &str = "org.willemaw.newsly";
const DEBUG_SESSION_PATH: &str = "/auth/debug/new-user";
const SHARE_ACTION_PATH: &str = "/api/share-actions";
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct IosE2eUserManifest {
    pub(crate) id: i64,
    pub(crate) email: String,
    pub(crate) debug_session_request: Value,
    pub(crate) debug_login_url: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct IosE2eApiManifest {
    pub(crate) base_url: String,
    pub(crate) debug_session_path: String,
    pub(crate) endpoints: BTreeMap<String, String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct IosE2eShareManifest {
    pub(crate) seeded_task_id: i64,
    pub(crate) seeded_action_id: i64,
    pub(crate) supported_modes: Vec<String>,
    pub(crate) example_request: Value,
    pub(crate) accepted_status: u16,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct IosE2eFixtureManifest {
    pub(crate) schema_version: u8,
    pub(crate) fixture_type: String,
    pub(crate) namespace: String,
    pub(crate) database: E2eDatabaseIdentity,
    pub(crate) user: IosE2eUserManifest,
    pub(crate) api: IosE2eApiManifest,
    pub(crate) content: newsly_db::IosE2eContentFixtures,
    pub(crate) briefing: newsly_db::IosE2eBriefingFixtures,
    pub(crate) chat: newsly_db::IosE2eChatFixtures,
    pub(crate) learning: newsly_db::IosE2eLearningFixtures,
    pub(crate) tasks: newsly_db::IosE2eTaskFixtures,
    pub(crate) share_api: IosE2eShareManifest,
    pub(crate) maestro_env: BTreeMap<String, String>,
}

impl IosE2eFixtureManifest {
    pub(crate) fn render_text(&self) -> String {
        format!(
            "seeded iOS E2E fixture namespace={} database={} user_id={} content_id={} chat_session_id={}; rerun with --output json for the complete manifest",
            self.namespace,
            self.database.database_name,
            self.user.id,
            self.content.detail_content_id,
            self.chat.session_id,
        )
    }
}

/// Seed the deterministic local iOS fixture and return its launch manifest.
///
/// # Errors
///
/// Returns an error unless the caller explicitly confirms a local environment, the database is
/// local, fixture paths are safe, every artifact is written, and the database seed commits.
pub(crate) async fn seed(
    pool: &PgPool,
    namespace: &str,
    server_port: u16,
    confirm_local: bool,
) -> Result<IosE2eFixtureManifest> {
    if !confirm_local {
        bail!("refusing fixture writes without --confirm-local");
    }
    let environment = env::var("ENVIRONMENT").unwrap_or_else(|_| "development".to_owned());
    require_local_environment(&environment)?;
    let database = inspect_e2e_database_identity(pool).await?;
    require_local_database(&database)?;
    let namespace = IosE2eFixtureNamespace::parse(namespace)?;
    let artifacts = ios_e2e_local_artifact_plan(&namespace);
    write_local_artifacts(&artifacts).await?;
    let receipt = seed_ios_e2e_fixture(pool, &namespace, &artifacts).await?;
    Ok(build_manifest(database, receipt, server_port))
}

async fn write_local_artifacts(artifacts: &IosE2eLocalArtifactPlan) -> Result<()> {
    let provider = env::var("CONTENT_BODY_STORAGE_PROVIDER").unwrap_or_else(|_| "local".to_owned());
    if provider.trim() != "local" {
        bail!("iOS E2E fixtures require CONTENT_BODY_STORAGE_PROVIDER=local");
    }
    let content_root = local_root("CONTENT_BODY_LOCAL_ROOT", "data/content_bodies")?;
    let media_root = local_root("MEDIA_BASE_DIR", "data/media")?;

    let index = resolve_artifact_path(&content_root, &artifacts.deck_object_key)?;
    let notes = resolve_artifact_path(&content_root, &artifacts.source_notes_object_key)?;
    let notes_html = resolve_artifact_path(&content_root, &artifacts.source_notes_html_object_key)?;
    let audio = resolve_artifact_path(&media_root, &artifacts.audio_storage_path)?;
    for path in [&index, &notes, &notes_html, &audio] {
        let parent = path
            .parent()
            .context("fixture artifact path must have a parent")?;
        tokio::fs::create_dir_all(parent)
            .await
            .with_context(|| format!("could not create fixture directory {}", parent.display()))?;
    }
    tokio::fs::write(&index, deck_fixture_html())
        .await
        .with_context(|| format!("could not write fixture Learning Deck {}", index.display()))?;
    tokio::fs::write(
        &notes,
        "# Reliable async systems fixture\n\nPrepare immutable input, release the transaction, and finalize in a fresh fenced transaction.\n",
    )
    .await
    .with_context(|| format!("could not write fixture source notes {}", notes.display()))?;
    tokio::fs::write(
        &notes_html,
        "<!doctype html><html><body><h1>Reliable async systems source notes</h1><p>Use short prepare and finalize transactions around external work.</p></body></html>",
    )
    .await
    .with_context(|| {
        format!(
            "could not write fixture source notes HTML {}",
            notes_html.display()
        )
    })?;
    tokio::fs::write(&audio, silent_wav())
        .await
        .with_context(|| format!("could not write fixture narration {}", audio.display()))?;
    Ok(())
}

fn local_root(variable: &'static str, default: &'static str) -> Result<PathBuf> {
    let configured = env::var_os(variable).map_or_else(|| PathBuf::from(default), PathBuf::from);
    let root = if configured.is_absolute() {
        configured
    } else {
        env::current_dir()?.join(configured)
    };
    if root == Path::new("/")
        || !root.is_absolute()
        || root.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::CurDir | Component::Prefix(_)
            )
        })
    {
        bail!("{variable} must resolve to a safe absolute local directory");
    }
    Ok(root)
}

fn resolve_artifact_path(root: &Path, relative: &str) -> Result<PathBuf> {
    let relative = Path::new(relative);
    if relative.is_absolute()
        || relative.as_os_str().is_empty()
        || relative
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        bail!("fixture artifact path is unsafe: {}", relative.display());
    }
    Ok(root.join(relative))
}

fn deck_fixture_html() -> &'static str {
    r#"<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>A Practical Playbook for Reliable Async Systems</title></head>
<body style="font-family:-apple-system,sans-serif;background:#10141c;color:#f7f7f7;margin:0;padding:32px">
<main><p>NEWSLY LEARNING DECK</p><h1>A Practical Playbook for Reliable Async Systems</h1>
<section><h2>Keep transactions short</h2><p>Prepare an immutable DTO, perform external work without a session, then finalize behind a fresh lease-fenced transaction.</p></section>
<section><h2>Make contracts generated</h2><p>Use the Rust OpenAPI document as the only client wire authority.</p></section></main>
</body></html>"#
}

fn silent_wav() -> Vec<u8> {
    const SAMPLE_RATE: u32 = 8_000;
    const SAMPLE_COUNT: u32 = SAMPLE_RATE;
    const DATA_BYTES: u32 = SAMPLE_COUNT * 2;
    let mut bytes = Vec::with_capacity((44 + DATA_BYTES) as usize);
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + DATA_BYTES).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&SAMPLE_RATE.to_le_bytes());
    bytes.extend_from_slice(&(SAMPLE_RATE * 2).to_le_bytes());
    bytes.extend_from_slice(&2_u16.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&DATA_BYTES.to_le_bytes());
    bytes.resize((44 + DATA_BYTES) as usize, 0);
    bytes
}

fn build_manifest(
    database: E2eDatabaseIdentity,
    receipt: IosE2eFixtureSeedReceipt,
    server_port: u16,
) -> IosE2eFixtureManifest {
    let host = "127.0.0.1";
    let base_url = format!("http://{host}:{server_port}");
    let debug_login_url = format!(
        "newsly://debug-login?user_id={}&host={host}&port={server_port}&https=false",
        receipt.user_id
    );
    let endpoints = manifest_endpoints(&receipt);
    let maestro_env = maestro_environment(&receipt, host, server_port);
    IosE2eFixtureManifest {
        schema_version: 1,
        fixture_type: "newsly.ios_e2e.fixture".to_owned(),
        namespace: receipt.namespace.clone(),
        user: IosE2eUserManifest {
            id: receipt.user_id,
            email: receipt.user_email,
            debug_session_request: json!({
                "user_id": receipt.user_id,
                "has_completed_onboarding": true,
                "has_completed_new_user_tutorial": true,
                "reading_experience": "briefing"
            }),
            debug_login_url,
        },
        api: IosE2eApiManifest {
            base_url,
            debug_session_path: DEBUG_SESSION_PATH.to_owned(),
            endpoints,
        },
        share_api: IosE2eShareManifest {
            seeded_task_id: receipt.tasks.share_task_id,
            seeded_action_id: receipt.tasks.share_action_id,
            supported_modes: LlmTaskMode::SHARE_ACTIONS
                .iter()
                .map(|mode| mode.as_str().to_owned())
                .collect(),
            example_request: json!({
                "url": format!(
                    "https://fixtures.newsly.invalid/{}/knowledge",
                    receipt.namespace
                ),
                "mode": "bookmark_only",
                "instruction": "Save this deterministic fixture to Knowledge",
                "chat_initial_message": Value::Null,
                "interests_prompt": Value::Null
            }),
            accepted_status: 202,
        },
        database,
        content: receipt.content,
        briefing: receipt.briefing,
        chat: receipt.chat,
        learning: receipt.learning,
        tasks: receipt.tasks,
        maestro_env,
    }
}

fn manifest_endpoints(receipt: &IosE2eFixtureSeedReceipt) -> BTreeMap<String, String> {
    BTreeMap::from([
        (
            "audio_episode".to_owned(),
            format!(
                "/api/content/audio-episodes/{}",
                receipt.learning.audio_episode_id
            ),
        ),
        ("briefing".to_owned(), "/api/briefing".to_owned()),
        (
            "briefing_lens".to_owned(),
            format!("/api/briefing/lenses/{}", receipt.briefing.lens_key),
        ),
        (
            "chat_session".to_owned(),
            format!("/api/content/chat/sessions/{}", receipt.chat.session_id),
        ),
        (
            "content_detail".to_owned(),
            format!("/api/content/{}", receipt.content.detail_content_id),
        ),
        (
            "knowledge".to_owned(),
            "/api/content/knowledge/list".to_owned(),
        ),
        (
            "learning_deck".to_owned(),
            format!("/api/learning/decks/{}", receipt.learning.deck_id),
        ),
        (
            "processing_stats".to_owned(),
            "/api/content/stats/processing-count".to_owned(),
        ),
        ("share_actions".to_owned(), SHARE_ACTION_PATH.to_owned()),
        (
            "submissions".to_owned(),
            "/api/content/submissions/list".to_owned(),
        ),
    ])
}

fn maestro_environment(
    receipt: &IosE2eFixtureSeedReceipt,
    host: &str,
    server_port: u16,
) -> BTreeMap<String, String> {
    BTreeMap::from([
        ("APP_ID".to_owned(), APP_BUNDLE_ID.to_owned()),
        (
            "AUDIO_EPISODE_ID".to_owned(),
            receipt.learning.audio_episode_id.to_string(),
        ),
        (
            "ARTICLE_TITLE".to_owned(),
            receipt.content.detail_title.clone(),
        ),
        (
            "ARTICLE_SEGMENT_ID".to_owned(),
            receipt.briefing.segment_id.to_string(),
        ),
        (
            "CHAT_SESSION_ID".to_owned(),
            receipt.chat.session_id.to_string(),
        ),
        (
            "ASSISTANT_REPLY".to_owned(),
            receipt.chat.expected_assistant_message.clone(),
        ),
        (
            "BASELINE_DIR".to_owned(),
            "client/newsly/Maestro/baselines".to_owned(),
        ),
        (
            "CONTENT_ID".to_owned(),
            receipt.content.detail_content_id.to_string(),
        ),
        (
            "CONTENT_TITLE".to_owned(),
            receipt.content.detail_title.clone(),
        ),
        ("DECK_ID".to_owned(), receipt.learning.deck_id.to_string()),
        (
            "FOLLOW_UP".to_owned(),
            "What should I verify next?".to_owned(),
        ),
        (
            "FOLLOW_UP_REPLY".to_owned(),
            "Verify the smallest observable behavior first.".to_owned(),
        ),
        (
            "LATEST_ASSISTANT".to_owned(),
            receipt.chat.expected_assistant_message.clone(),
        ),
        (
            "LONG_CONTENT_ID".to_owned(),
            receipt.content.knowledge_content_id.to_string(),
        ),
        (
            "NEWS_ITEM_ID".to_owned(),
            receipt.content.detail_content_id.to_string(),
        ),
        (
            "NEWS_SEGMENT_ID".to_owned(),
            receipt.briefing.segment_id.to_string(),
        ),
        (
            "OLDER_USER".to_owned(),
            receipt.chat.expected_user_message.clone(),
        ),
        (
            "PODCAST_SEGMENT_ID".to_owned(),
            receipt.briefing.segment_id.to_string(),
        ),
        (
            "PODCAST_TITLE".to_owned(),
            receipt.learning.audio_title.clone(),
        ),
        ("QUERY".to_owned(), "reliable async systems".to_owned()),
        ("RECORD_SCREENSHOTS".to_owned(), "false".to_owned()),
        (
            "SEGMENT_ID".to_owned(),
            receipt.briefing.segment_id.to_string(),
        ),
        ("SERVER_HOST".to_owned(), host.to_owned()),
        ("SERVER_PORT".to_owned(), server_port.to_string()),
        (
            "TOPIC_STANCE".to_owned(),
            "Prefer bounded, observable retries.".to_owned(),
        ),
        (
            "TOPIC_SUMMARY".to_owned(),
            "Reliable asynchronous systems".to_owned(),
        ),
        (
            "TRANSCRIPT".to_owned(),
            "Summarize the reliability lessons.".to_owned(),
        ),
        ("USER_ID".to_owned(), receipt.user_id.to_string()),
        ("VISUAL_NOW".to_owned(), receipt.seeded_at.to_rfc3339()),
    ])
}

fn require_local_environment(environment: &str) -> Result<()> {
    let normalized = environment.trim().to_ascii_lowercase();
    if matches!(
        normalized.as_str(),
        "development" | "dev" | "local" | "test" | "testing"
    ) {
        return Ok(());
    }
    bail!(
        "refusing iOS E2E fixture writes when ENVIRONMENT={environment:?}; expected development, local, or test"
    )
}

fn require_local_database(identity: &E2eDatabaseIdentity) -> Result<()> {
    let Some(address) = identity.server_address.as_deref() else {
        return Ok(());
    };
    let address = address
        .parse::<IpAddr>()
        .with_context(|| format!("PostgreSQL reported invalid server address {address:?}"))?;
    if !address.is_loopback() {
        bail!(
            "refusing iOS E2E fixture writes to non-loopback PostgreSQL server {}",
            identity
                .server_address
                .as_deref()
                .expect("checked as present")
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;
    use std::fs;

    use chrono::{Duration, TimeZone, Utc};

    use super::*;

    #[test]
    fn environment_guard_is_fail_closed() {
        for allowed in ["development", "DEV", "local", "test", "testing"] {
            require_local_environment(allowed).expect("local environment is allowed");
        }
        for rejected in ["production", "staging", "", "preview"] {
            assert!(require_local_environment(rejected).is_err(), "{rejected:?}");
        }
    }

    #[test]
    fn database_guard_accepts_only_loopback_or_local_socket() {
        for server_address in [None, Some("127.0.0.1"), Some("::1")] {
            require_local_database(&E2eDatabaseIdentity {
                database_name: "newsly_test".to_owned(),
                server_address: server_address.map(ToOwned::to_owned),
            })
            .expect("local database target is allowed");
        }
        assert!(
            require_local_database(&E2eDatabaseIdentity {
                database_name: "newsly".to_owned(),
                server_address: Some("10.0.0.4".to_owned()),
            })
            .is_err()
        );
    }

    #[test]
    fn maestro_manifest_supplies_every_flow_variable() {
        let seeded_at = Utc.with_ymd_and_hms(2026, 9, 23, 10, 30, 0).unwrap();
        let receipt = IosE2eFixtureSeedReceipt {
            namespace: "contract".to_owned(),
            seeded_at,
            user_id: 7,
            user_email: "fixture@example.test".to_owned(),
            content: newsly_db::IosE2eContentFixtures {
                detail_content_id: 11,
                detail_title: "Detail".to_owned(),
                knowledge_content_id: 12,
                knowledge_title: "Knowledge".to_owned(),
                processing_content_id: 13,
                processing_title: "Processing".to_owned(),
            },
            briefing: newsly_db::IosE2eBriefingFixtures {
                lens_id: 21,
                lens_key: "articles".to_owned(),
                segment_id: 22,
            },
            chat: newsly_db::IosE2eChatFixtures {
                session_id: 31,
                title: "Chat".to_owned(),
                expected_user_message: "Question".to_owned(),
                expected_assistant_message: "Answer".to_owned(),
            },
            learning: newsly_db::IosE2eLearningFixtures {
                deck_id: 41,
                deck_title: "Deck".to_owned(),
                viewer_available: true,
                audio_episode_id: 42,
                audio_title: "Audio".to_owned(),
                audio_file_available: true,
            },
            tasks: newsly_db::IosE2eTaskFixtures {
                deferred_processing_task_id: 51,
                deferred_until: seeded_at + Duration::hours(1),
                share_task_id: 52,
                share_action_id: 53,
            },
        };
        let environment = maestro_environment(&receipt, "127.0.0.1", 8000);
        let flows =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../client/newsly/Maestro/flows");
        let mut required = BTreeSet::new();
        for entry in fs::read_dir(flows).expect("Maestro flow directory should exist") {
            let path = entry.expect("flow entry should be readable").path();
            if path.extension().and_then(|value| value.to_str()) != Some("yaml") {
                continue;
            }
            let contents = fs::read_to_string(path).expect("Maestro flow should be readable");
            for suffix in contents.split("${").skip(1) {
                let variable = suffix
                    .chars()
                    .take_while(|character| character.is_ascii_uppercase() || *character == '_')
                    .collect::<String>();
                if !variable.is_empty() {
                    required.insert(variable);
                }
            }
        }
        let missing = required
            .into_iter()
            .filter(|variable| !environment.contains_key(variable))
            .collect::<Vec<_>>();
        assert!(missing.is_empty(), "missing Maestro variables: {missing:?}");
    }
}
