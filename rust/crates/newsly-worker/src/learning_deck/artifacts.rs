use std::collections::BTreeSet;
use std::env;
use std::fmt::Write as _;
use std::path::{Component, Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use newsly_db::StoredLearningDeckArtifact;
use object_store::aws::AmazonS3Builder;
use object_store::path::Path as ObjectPath;
use object_store::{
    Attribute, AttributeValue, Attributes, ClientOptions, Error as ObjectStoreError, ObjectStore,
    ObjectStoreExt, PutOptions,
};
use regex::Regex;
use reqwest::Url;
use secrecy::{ExposeSecret, SecretString};
use serde_json::Value;
use thiserror::Error;

pub(super) const OUTPUT_INDEX_HTML: &str = "output/index.html";
pub(super) const OUTPUT_SOURCE_NOTES: &str = "output/source-notes.md";
pub(super) const OUTPUT_SOURCE_METADATA: &str = "output/source-metadata.json";
pub(super) const OUTPUT_ASSET_DIRECTORY: &str = "output/assets";

const REVEAL_VERSION: &str = "6.0.1";
const RESPONSIVE_LAYOUT_VERSION: &str = "responsive-v2";
const RESPONSIVE_LAYOUT_META_NAME: &str = "newsly-deck-layout";
const HTML_CONTENT_TYPE: &str = "text/html; charset=utf-8";
const MARKDOWN_CONTENT_TYPE: &str = "text/markdown; charset=utf-8";
const JSONL_CONTENT_TYPE: &str = "application/x-ndjson; charset=utf-8";
const ALLOWED_SCRIPT_PACKAGES: [&str; 5] = ["reveal.js", "react", "react-dom", "d3", "mermaid"];
const ALLOWED_SCRIPT_HOSTS: [&str; 2] = ["cdn.jsdelivr.net", "unpkg.com"];

#[derive(Debug, Clone)]
pub(super) struct LearningDeckArtifactLimits {
    pub index_html_bytes: usize,
    pub source_notes_bytes: usize,
    pub asset_count: usize,
    pub asset_bytes: usize,
}

impl LearningDeckArtifactLimits {
    pub(super) fn from_env() -> Result<Self, LearningDeckArtifactError> {
        Ok(Self {
            index_html_bytes: bounded_usize(
                "LEARNING_DECK_MAX_INDEX_HTML_BYTES",
                2_000_000,
                10_000,
                10_000_000,
            )?,
            source_notes_bytes: bounded_usize(
                "LEARNING_DECK_MAX_SOURCE_NOTES_BYTES",
                1_000_000,
                1_000,
                5_000_000,
            )?,
            asset_count: bounded_usize("LEARNING_DECK_MAX_ASSET_COUNT", 40, 0, 200)?,
            asset_bytes: bounded_usize(
                "LEARNING_DECK_MAX_ASSET_BYTES",
                5_000_000,
                1_000,
                20_000_000,
            )?,
        })
    }
}

#[derive(Debug, Clone)]
pub(super) struct LearningDeckAsset {
    pub relative_path: String,
    pub bytes: Vec<u8>,
    pub content_type: String,
}

#[derive(Clone)]
pub(super) struct LearningDeckArtifactStore {
    backend: ArtifactBackend,
    prefix: String,
    limits: LearningDeckArtifactLimits,
}

impl std::fmt::Debug for LearningDeckArtifactStore {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("LearningDeckArtifactStore")
            .field("backend", &self.backend)
            .field("prefix", &self.prefix)
            .field("limits", &self.limits)
            .finish()
    }
}

#[derive(Clone)]
enum ArtifactBackend {
    Local { root: PathBuf },
    S3 { store: Arc<dyn ObjectStore> },
}

impl std::fmt::Debug for ArtifactBackend {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Local { root } => formatter
                .debug_struct("LocalArtifactBackend")
                .field("root", root)
                .finish(),
            Self::S3 { .. } => formatter.debug_struct("S3ArtifactBackend").finish(),
        }
    }
}

impl LearningDeckArtifactStore {
    pub(super) fn from_env() -> Result<Self, LearningDeckArtifactError> {
        let provider = env::var("CONTENT_BODY_STORAGE_PROVIDER")
            .unwrap_or_else(|_| "local".to_owned())
            .trim()
            .to_owned();
        let backend = match provider.as_str() {
            "local" => {
                let configured = env::var_os("CONTENT_BODY_LOCAL_ROOT")
                    .map_or_else(|| PathBuf::from("data/content_bodies"), PathBuf::from);
                let root = if configured.is_absolute() {
                    configured
                } else {
                    env::current_dir()?.join(configured)
                };
                validate_root(&root)?;
                ArtifactBackend::Local { root }
            }
            "s3_compatible" => {
                let bucket = required("CONTENT_BODY_STORAGE_BUCKET")?;
                let endpoint = optional_url("CONTENT_BODY_STORAGE_ENDPOINT")?;
                let region = optional("CONTENT_BODY_STORAGE_REGION");
                let access_key =
                    optional("CONTENT_BODY_STORAGE_ACCESS_KEY").map(SecretString::from);
                let secret_key =
                    optional("CONTENT_BODY_STORAGE_SECRET_KEY").map(SecretString::from);
                if access_key.is_some() != secret_key.is_some() {
                    return Err(LearningDeckArtifactError::IncompleteCredentials);
                }
                let timeout_seconds =
                    bounded_u64("CONTENT_BODY_STORAGE_TIMEOUT_SECONDS", 30, 1, 300)?;
                let mut builder = AmazonS3Builder::from_env()
                    .with_bucket_name(bucket)
                    .with_disable_bulk_delete(true)
                    .with_client_options(
                        ClientOptions::new().with_timeout(Duration::from_secs(timeout_seconds)),
                    );
                if let Some(endpoint) = endpoint {
                    builder = builder
                        .with_allow_http(endpoint.scheme() == "http")
                        .with_endpoint(endpoint.to_string());
                }
                if let Some(region) = region {
                    builder = builder.with_region(region);
                }
                if let (Some(access_key), Some(secret_key)) = (access_key, secret_key) {
                    builder = builder
                        .with_access_key_id(access_key.expose_secret())
                        .with_secret_access_key(secret_key.expose_secret());
                }
                let store = builder
                    .build()
                    .map_err(|error| LearningDeckArtifactError::ObjectStore(error.to_string()))?;
                ArtifactBackend::S3 {
                    store: Arc::new(store),
                }
            }
            _ => return Err(LearningDeckArtifactError::UnsupportedProvider(provider)),
        };
        let prefix = normalize_prefix(
            &env::var("CONTENT_BODY_STORAGE_PREFIX").unwrap_or_else(|_| "content".to_owned()),
        )?;
        Ok(Self {
            backend,
            prefix,
            limits: LearningDeckArtifactLimits::from_env()?,
        })
    }

    pub(super) async fn get_text(
        &self,
        key: &str,
    ) -> Result<Option<String>, LearningDeckArtifactError> {
        let Some(bytes) = self.get_bytes(key).await? else {
            return Ok(None);
        };
        String::from_utf8(bytes)
            .map(Some)
            .map_err(LearningDeckArtifactError::Utf8)
    }

    pub(super) async fn store_agent_log(
        &self,
        user_id: i64,
        deck_id: i64,
        run_id: i64,
        events: &[Value],
    ) -> Result<Option<String>, LearningDeckArtifactError> {
        if events.is_empty() {
            return Ok(None);
        }
        let key = join_key(
            &self.prefix,
            &format!(
                "learning_deck_internal_logs/{user_id}/{deck_id}/runs/{run_id}/agent-log.jsonl"
            ),
        )?;
        let mut payload = String::new();
        for event in events {
            payload.push_str(&serde_json::to_string(event)?);
            payload.push('\n');
        }
        self.put_bytes(&key, payload.into_bytes(), JSONL_CONTENT_TYPE)
            .await?;
        Ok(Some(key))
    }

    #[allow(clippy::too_many_arguments)]
    pub(super) async fn store_bundle(
        &self,
        user_id: i64,
        deck_id: i64,
        run_id: i64,
        index_html: &str,
        source_notes_md: &str,
        assets: &[LearningDeckAsset],
    ) -> Result<StoredLearningDeckArtifact, LearningDeckArtifactError> {
        validate_learning_deck_artifact(index_html, source_notes_md, &self.limits)?;
        if assets.len() > self.limits.asset_count {
            return Err(LearningDeckArtifactError::Contract(
                "artifact bundle has too many local assets".to_owned(),
            ));
        }
        let storage_prefix = join_key(
            &self.prefix,
            &format!("learning_decks/{user_id}/{deck_id}/runs/{run_id}"),
        )?;
        let deck_object_key = join_key(&storage_prefix, "index.html")?;
        let source_notes_object_key = join_key(&storage_prefix, "source-notes.md")?;
        let source_notes_html_object_key = join_key(&storage_prefix, "source-notes.html")?;
        let source_notes_html = render_source_notes_html(
            source_notes_md,
            &format!("Learning Deck {deck_id} Source Notes"),
        );
        let mut written = Vec::new();
        let writes = [
            (
                deck_object_key.clone(),
                index_html.as_bytes().to_vec(),
                HTML_CONTENT_TYPE,
            ),
            (
                source_notes_object_key.clone(),
                source_notes_md.as_bytes().to_vec(),
                MARKDOWN_CONTENT_TYPE,
            ),
            (
                source_notes_html_object_key.clone(),
                source_notes_html.into_bytes(),
                HTML_CONTENT_TYPE,
            ),
        ];
        for (key, bytes, content_type) in writes {
            if let Err(error) = self.put_bytes(&key, bytes, content_type).await {
                self.best_effort_delete(&written).await;
                return Err(error);
            }
            written.push(key);
        }
        let mut thumbnail_object_key = None;
        for asset in assets {
            if asset.bytes.len() > self.limits.asset_bytes {
                self.best_effort_delete(&written).await;
                return Err(LearningDeckArtifactError::Contract(format!(
                    "artifact asset is too large: {}",
                    asset.relative_path
                )));
            }
            let relative = normalize_asset_path(&asset.relative_path)?;
            let key = join_key(&storage_prefix, &relative)?;
            if let Err(error) = self
                .put_bytes(&key, asset.bytes.clone(), &asset.content_type)
                .await
            {
                self.best_effort_delete(&written).await;
                return Err(error);
            }
            if relative == "assets/thumbnail.png" {
                thumbnail_object_key = Some(key.clone());
            }
            written.push(key);
        }
        for key in [
            &deck_object_key,
            &source_notes_object_key,
            &source_notes_html_object_key,
        ] {
            if !self.exists(key).await? {
                self.best_effort_delete(&written).await;
                return Err(LearningDeckArtifactError::ObjectUnavailable(key.clone()));
            }
        }
        Ok(StoredLearningDeckArtifact {
            storage_prefix,
            deck_object_key,
            source_notes_object_key,
            source_notes_html_object_key,
            thumbnail_object_key,
            artifact_object_keys: written,
        })
    }

    pub(super) async fn delete_many(
        &self,
        keys: &[String],
    ) -> Result<(), LearningDeckArtifactError> {
        for key in keys.iter().collect::<BTreeSet<_>>() {
            self.delete(key).await?;
        }
        Ok(())
    }

    async fn best_effort_delete(&self, keys: &[String]) {
        if let Err(error) = self.delete_many(keys).await {
            tracing::warn!(error = %error, "failed to clean a partial Learning Deck artifact bundle");
        }
    }

    async fn put_bytes(
        &self,
        key: &str,
        bytes: Vec<u8>,
        content_type: &str,
    ) -> Result<(), LearningDeckArtifactError> {
        validate_object_key(key)?;
        match &self.backend {
            ArtifactBackend::Local { root } => write_local(root, key, bytes).await,
            ArtifactBackend::S3 { store } => {
                let path = ObjectPath::parse(key).map_err(|error| {
                    LearningDeckArtifactError::UnsafeObjectKey(error.to_string())
                })?;
                let attributes = Attributes::from_iter([(
                    Attribute::ContentType,
                    AttributeValue::from(content_type.to_owned()),
                )]);
                store
                    .put_opts(
                        &path,
                        bytes.into(),
                        PutOptions {
                            attributes,
                            ..PutOptions::default()
                        },
                    )
                    .await
                    .map(|_| ())
                    .map_err(|error| LearningDeckArtifactError::ObjectStore(error.to_string()))
            }
        }
    }

    async fn get_bytes(&self, key: &str) -> Result<Option<Vec<u8>>, LearningDeckArtifactError> {
        validate_object_key(key)?;
        match &self.backend {
            ArtifactBackend::Local { root } => read_local(root, key).await,
            ArtifactBackend::S3 { store } => {
                let path = ObjectPath::parse(key).map_err(|error| {
                    LearningDeckArtifactError::UnsafeObjectKey(error.to_string())
                })?;
                let result = match store.get(&path).await {
                    Ok(result) => result,
                    Err(ObjectStoreError::NotFound { .. }) => return Ok(None),
                    Err(error) => {
                        return Err(LearningDeckArtifactError::ObjectStore(error.to_string()));
                    }
                };
                result
                    .bytes()
                    .await
                    .map(|bytes| Some(bytes.to_vec()))
                    .map_err(|error| LearningDeckArtifactError::ObjectStore(error.to_string()))
            }
        }
    }

    async fn exists(&self, key: &str) -> Result<bool, LearningDeckArtifactError> {
        validate_object_key(key)?;
        match &self.backend {
            ArtifactBackend::Local { root } => {
                let path = safe_existing_local_path(root, key).await?;
                Ok(path.is_some())
            }
            ArtifactBackend::S3 { store } => {
                let path = ObjectPath::parse(key).map_err(|error| {
                    LearningDeckArtifactError::UnsafeObjectKey(error.to_string())
                })?;
                match store.head(&path).await {
                    Ok(_) => Ok(true),
                    Err(ObjectStoreError::NotFound { .. }) => Ok(false),
                    Err(error) => Err(LearningDeckArtifactError::ObjectStore(error.to_string())),
                }
            }
        }
    }

    async fn delete(&self, key: &str) -> Result<(), LearningDeckArtifactError> {
        validate_object_key(key)?;
        match &self.backend {
            ArtifactBackend::Local { root } => {
                let Some(path) = safe_existing_local_path(root, key).await? else {
                    return Ok(());
                };
                match tokio::fs::remove_file(&path).await {
                    Ok(()) => Ok(()),
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
                    Err(source) => Err(LearningDeckArtifactError::File { path, source }),
                }
            }
            ArtifactBackend::S3 { store } => {
                let path = ObjectPath::parse(key).map_err(|error| {
                    LearningDeckArtifactError::UnsafeObjectKey(error.to_string())
                })?;
                match store.delete(&path).await {
                    Ok(()) | Err(ObjectStoreError::NotFound { .. }) => Ok(()),
                    Err(error) => Err(LearningDeckArtifactError::ObjectStore(error.to_string())),
                }
            }
        }
    }
}

pub(super) fn validate_learning_deck_artifact(
    index_html: &str,
    source_notes_md: &str,
    limits: &LearningDeckArtifactLimits,
) -> Result<(), LearningDeckArtifactError> {
    let mut errors = Vec::new();
    let lower = index_html.to_ascii_lowercase();
    if index_html.trim().is_empty() {
        errors.push("index.html is empty".to_owned());
    }
    if source_notes_md.trim().is_empty() {
        errors.push("source-notes.md is empty".to_owned());
    }
    if index_html.len() > limits.index_html_bytes {
        errors.push("index.html exceeds configured size limit".to_owned());
    }
    if source_notes_md.len() > limits.source_notes_bytes {
        errors.push("source-notes.md exceeds configured size limit".to_owned());
    }
    if !lower.contains("<section") || !lower.contains("reveal") {
        errors.push("index.html does not look like a Reveal.js deck".to_owned());
    }
    if !has_responsive_layout(index_html)? {
        errors.push(format!(
            "index.html must declare the {RESPONSIVE_LAYOUT_VERSION} Learning Deck layout metadata"
        ));
    }
    let event_attribute = Regex::new(r"(?i)\son[a-z]+\s*=")?;
    if event_attribute.is_match(index_html) {
        errors.push("index.html contains inline event-handler attributes".to_owned());
    }
    if !has_custom_style(index_html)? {
        errors
            .push("index.html must include custom deck styling beyond stock Reveal.js".to_owned());
    }
    for source in extract_attribute(index_html, "script", "src")? {
        if !allowed_script_source(&source) {
            errors.push(format!(
                "index.html contains disallowed script source: {source}"
            ));
        }
    }
    for stylesheet in stylesheet_hrefs(index_html)? {
        if is_reveal_cdn_url(&stylesheet) && !supported_reveal_url(&stylesheet) {
            errors.push(format!(
                "index.html must pin Reveal.js stylesheets to version {REVEAL_VERSION}: {stylesheet}"
            ));
        }
    }
    let source_heading = Regex::new(r"(?im)^#{1,3}\s+source")?;
    if !source_heading.is_match(source_notes_md) {
        errors.push("source-notes.md must include a source section".to_owned());
    }
    append_secret_and_host_path_errors(&mut errors, index_html, source_notes_md);
    if errors.is_empty() {
        Ok(())
    } else {
        Err(LearningDeckArtifactError::RepairableContract(errors))
    }
}

fn has_responsive_layout(html: &str) -> Result<bool, LearningDeckArtifactError> {
    let meta = Regex::new(r"(?is)<meta\b[^>]*>")?;
    let name = Regex::new(r#"(?i)\bname\s*=\s*['\"]([^'\"]+)['\"]"#)?;
    let content = Regex::new(r#"(?i)\bcontent\s*=\s*['\"]([^'\"]+)['\"]"#)?;
    Ok(meta.find_iter(html).any(|tag| {
        name.captures(tag.as_str())
            .and_then(|capture| capture.get(1))
            .is_some_and(|value| {
                value
                    .as_str()
                    .trim()
                    .eq_ignore_ascii_case(RESPONSIVE_LAYOUT_META_NAME)
            })
            && content
                .captures(tag.as_str())
                .and_then(|capture| capture.get(1))
                .is_some_and(|value| {
                    value
                        .as_str()
                        .trim()
                        .eq_ignore_ascii_case(RESPONSIVE_LAYOUT_VERSION)
                })
    }))
}

fn has_custom_style(html: &str) -> Result<bool, LearningDeckArtifactError> {
    if Regex::new(r"(?is)<style\b[^>]*>.*?</style>")?.is_match(html) {
        return Ok(true);
    }
    Ok(stylesheet_hrefs(html)?
        .into_iter()
        .any(|href| !is_reveal_cdn_url(&href)))
}

fn stylesheet_hrefs(html: &str) -> Result<Vec<String>, LearningDeckArtifactError> {
    let links = Regex::new(r"(?is)<link\b[^>]*>")?;
    let rel = Regex::new(r#"(?i)\brel\s*=\s*['\"]([^'\"]+)['\"]"#)?;
    let href = Regex::new(r#"(?i)\bhref\s*=\s*['\"]([^'\"]+)['\"]"#)?;
    Ok(links
        .find_iter(html)
        .filter_map(|tag| {
            let rel = rel.captures(tag.as_str())?.get(1)?.as_str();
            rel.split_ascii_whitespace()
                .any(|value| value.eq_ignore_ascii_case("stylesheet"))
                .then(|| {
                    href.captures(tag.as_str())?
                        .get(1)
                        .map(|value| value.as_str().to_owned())
                })
                .flatten()
        })
        .collect())
}

fn extract_attribute(
    html: &str,
    tag: &str,
    attribute: &str,
) -> Result<Vec<String>, LearningDeckArtifactError> {
    let tags = Regex::new(&format!(r"(?is)<{tag}\b[^>]*>"))?;
    let attribute = Regex::new(&format!(r#"(?i)\b{attribute}\s*=\s*['\"]([^'\"]+)['\"]"#))?;
    Ok(tags
        .find_iter(html)
        .filter_map(|tag| {
            attribute
                .captures(tag.as_str())
                .and_then(|capture| capture.get(1))
                .map(|value| value.as_str().to_owned())
        })
        .collect())
}

fn allowed_script_source(source: &str) -> bool {
    let source = source.trim();
    if source.is_empty() || source.starts_with("//") {
        return false;
    }
    if let Ok(url) = Url::parse(source) {
        if url.scheme() != "https"
            || !url
                .host_str()
                .is_some_and(|host| ALLOWED_SCRIPT_HOSTS.contains(&host))
        {
            return false;
        }
        let package = cdn_package(&url);
        return package.as_deref().is_some_and(|package| {
            ALLOWED_SCRIPT_PACKAGES.contains(&package)
                && (package != "reveal.js" || supported_reveal_url(source))
        });
    }
    normalize_asset_path(source.split(['?', '#']).next().unwrap_or(source)).is_ok()
}

fn cdn_package(url: &Url) -> Option<String> {
    let parts = url
        .path_segments()?
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>();
    let segment = match url.host_str()? {
        "cdn.jsdelivr.net" if parts.first() == Some(&"npm") => *parts.get(1)?,
        "unpkg.com" => *parts.first()?,
        _ => return None,
    };
    if segment.starts_with('@') {
        let mut pieces = segment.rsplitn(2, '@').collect::<Vec<_>>();
        pieces.reverse();
        pieces.first().map(|value| (*value).to_owned())
    } else {
        Some(segment.split('@').next()?.to_owned())
    }
}

fn supported_reveal_url(source: &str) -> bool {
    let Ok(url) = Url::parse(source) else {
        return false;
    };
    match url.host_str() {
        Some("cdn.jsdelivr.net") => url
            .path()
            .starts_with(&format!("/npm/reveal.js@{REVEAL_VERSION}/")),
        Some("unpkg.com") => url
            .path()
            .starts_with(&format!("/reveal.js@{REVEAL_VERSION}/")),
        _ => false,
    }
}

fn is_reveal_cdn_url(source: &str) -> bool {
    Url::parse(source)
        .ok()
        .is_some_and(|url| cdn_package(&url).as_deref() == Some("reveal.js"))
}

fn append_secret_and_host_path_errors(errors: &mut Vec<String>, html: &str, notes: &str) {
    let combined = format!("{html}\n{notes}");
    for name in [
        "JWT_SECRET_KEY",
        "ADMIN_PASSWORD",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "EXA_API_KEY",
        "LLM_TASK_SANDBOX_E2B_API_KEY",
        "E2B_API_KEY",
    ] {
        if env::var(name)
            .ok()
            .is_some_and(|secret| secret.len() >= 12 && combined.contains(&secret))
        {
            errors.push("artifact appears to contain a configured secret value".to_owned());
            break;
        }
    }
    let mut suspicious = vec!["/Users/".to_owned()];
    if let Ok(current) = env::current_dir() {
        suspicious.push(current.to_string_lossy().into_owned());
    }
    for name in ["LOGS_DIR", "CONTENT_BODY_LOCAL_ROOT"] {
        if let Ok(value) = env::var(name)
            && !value.trim().is_empty()
        {
            suspicious.push(value);
        }
    }
    if suspicious
        .into_iter()
        .any(|path| !path.is_empty() && combined.contains(&path))
    {
        errors.push("artifact appears to expose backend host filesystem paths".to_owned());
    }
}

fn render_source_notes_html(markdown: &str, title: &str) -> String {
    let mut body = String::new();
    let mut in_list = false;
    let mut in_code = false;
    for line in markdown.lines() {
        if line.trim_start().starts_with("```") {
            if in_list {
                body.push_str("</ul>\n");
                in_list = false;
            }
            if in_code {
                body.push_str("</code></pre>\n");
            } else {
                body.push_str("<pre><code>");
            }
            in_code = !in_code;
            continue;
        }
        if in_code {
            body.push_str(&escape_html(line));
            body.push('\n');
            continue;
        }
        let trimmed = line.trim();
        if trimmed.is_empty() {
            if in_list {
                body.push_str("</ul>\n");
                in_list = false;
            }
            continue;
        }
        let heading = trimmed
            .chars()
            .take_while(|character| *character == '#')
            .count();
        if (1..=3).contains(&heading) && trimmed.as_bytes().get(heading) == Some(&b' ') {
            if in_list {
                body.push_str("</ul>\n");
                in_list = false;
            }
            writeln!(
                &mut body,
                "<h{heading}>{}</h{heading}>",
                escape_html(trimmed[heading + 1..].trim())
            )
            .expect("writing to a String cannot fail");
        } else if let Some(item) = trimmed
            .strip_prefix("- ")
            .or_else(|| trimmed.strip_prefix("* "))
        {
            if !in_list {
                body.push_str("<ul>\n");
                in_list = true;
            }
            writeln!(&mut body, "<li>{}</li>", escape_html(item))
                .expect("writing to a String cannot fail");
        } else {
            if in_list {
                body.push_str("</ul>\n");
                in_list = false;
            }
            writeln!(&mut body, "<p>{}</p>", escape_html(trimmed))
                .expect("writing to a String cannot fail");
        }
    }
    if in_list {
        body.push_str("</ul>\n");
    }
    if in_code {
        body.push_str("</code></pre>\n");
    }
    format!(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{}</title><style>body{{font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif;line-height:1.55;margin:0;padding:32px;color:#171717;background:#fff}}main{{max-width:880px;margin:0 auto}}pre,code{{background:#f4f4f5;border-radius:5px}}pre{{padding:12px;overflow-x:auto}}code{{padding:1px 4px}}</style></head><body><main>{body}</main></body></html>",
        escape_html(title)
    )
}

fn escape_html(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

fn normalize_asset_path(path: &str) -> Result<String, LearningDeckArtifactError> {
    let cleaned = path.trim().trim_start_matches('/');
    let path = Path::new(cleaned);
    if cleaned.is_empty()
        || cleaned.contains(['\0', '\\'])
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(LearningDeckArtifactError::UnsafeAssetPath(path.to_owned()));
    }
    let normalized = path.to_string_lossy().into_owned();
    if !normalized.starts_with("assets/") {
        return Err(LearningDeckArtifactError::UnsafeAssetPath(path.to_owned()));
    }
    Ok(normalized)
}

fn normalize_prefix(prefix: &str) -> Result<String, LearningDeckArtifactError> {
    let prefix = prefix.trim().trim_matches('/');
    if prefix.is_empty() {
        return Ok(String::new());
    }
    validate_object_key(prefix)?;
    Ok(prefix.to_owned())
}

fn join_key(prefix: &str, suffix: &str) -> Result<String, LearningDeckArtifactError> {
    let key = if prefix.is_empty() {
        suffix.trim_matches('/').to_owned()
    } else {
        format!("{}/{}", prefix.trim_matches('/'), suffix.trim_matches('/'))
    };
    validate_object_key(&key)?;
    Ok(key)
}

fn validate_root(root: &Path) -> Result<(), LearningDeckArtifactError> {
    if root == Path::new("/")
        || !root.is_absolute()
        || root.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::CurDir | Component::Prefix(_)
            )
        })
    {
        return Err(LearningDeckArtifactError::UnsafeRoot(root.to_path_buf()));
    }
    Ok(())
}

fn validate_object_key(key: &str) -> Result<(), LearningDeckArtifactError> {
    let path = Path::new(key);
    if key.is_empty()
        || key.len() > 2_048
        || key.contains(['\0', '\\'])
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(LearningDeckArtifactError::UnsafeObjectKey(key.to_owned()));
    }
    Ok(())
}

async fn write_local(
    root: &Path,
    key: &str,
    bytes: Vec<u8>,
) -> Result<(), LearningDeckArtifactError> {
    tokio::fs::create_dir_all(root)
        .await
        .map_err(|source| LearningDeckArtifactError::File {
            path: root.to_path_buf(),
            source,
        })?;
    let canonical_root =
        tokio::fs::canonicalize(root)
            .await
            .map_err(|source| LearningDeckArtifactError::File {
                path: root.to_path_buf(),
                source,
            })?;
    let path = root.join(key);
    let parent = path
        .parent()
        .ok_or_else(|| LearningDeckArtifactError::UnsafeLocalPath(path.clone()))?;
    tokio::fs::create_dir_all(parent)
        .await
        .map_err(|source| LearningDeckArtifactError::File {
            path: parent.to_path_buf(),
            source,
        })?;
    let canonical_parent = tokio::fs::canonicalize(parent).await.map_err(|source| {
        LearningDeckArtifactError::File {
            path: parent.to_path_buf(),
            source,
        }
    })?;
    if !canonical_parent.starts_with(&canonical_root) {
        return Err(LearningDeckArtifactError::UnsafeLocalPath(path));
    }
    let temporary = canonical_parent.join(format!(
        ".{}.{}.tmp",
        path.file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("artifact"),
        uuid::Uuid::new_v4().simple()
    ));
    tokio::fs::write(&temporary, bytes)
        .await
        .map_err(|source| LearningDeckArtifactError::File {
            path: temporary.clone(),
            source,
        })?;
    tokio::fs::rename(&temporary, &path)
        .await
        .map_err(|source| LearningDeckArtifactError::File { path, source })
}

async fn read_local(root: &Path, key: &str) -> Result<Option<Vec<u8>>, LearningDeckArtifactError> {
    let Some(path) = safe_existing_local_path(root, key).await? else {
        return Ok(None);
    };
    tokio::fs::read(&path)
        .await
        .map(Some)
        .map_err(|source| LearningDeckArtifactError::File { path, source })
}

async fn safe_existing_local_path(
    root: &Path,
    key: &str,
) -> Result<Option<PathBuf>, LearningDeckArtifactError> {
    let canonical_root = match tokio::fs::canonicalize(root).await {
        Ok(root) => root,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(source) => {
            return Err(LearningDeckArtifactError::File {
                path: root.to_path_buf(),
                source,
            });
        }
    };
    let requested = root.join(key);
    let path = match tokio::fs::canonicalize(&requested).await {
        Ok(path) => path,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(source) => {
            return Err(LearningDeckArtifactError::File {
                path: requested,
                source,
            });
        }
    };
    if !path.starts_with(&canonical_root) {
        return Err(LearningDeckArtifactError::UnsafeLocalPath(path));
    }
    Ok(Some(path))
}

fn required(name: &'static str) -> Result<String, LearningDeckArtifactError> {
    optional(name).ok_or(LearningDeckArtifactError::Missing(name))
}

fn optional(name: &'static str) -> Option<String> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn optional_url(name: &'static str) -> Result<Option<Url>, LearningDeckArtifactError> {
    optional(name)
        .map(|value| {
            let url = value
                .parse::<Url>()
                .map_err(|_| LearningDeckArtifactError::InvalidUrl(name, value.clone()))?;
            if !matches!(url.scheme(), "http" | "https") || url.cannot_be_a_base() {
                return Err(LearningDeckArtifactError::InvalidUrl(name, value));
            }
            Ok(url)
        })
        .transpose()
}

fn bounded_usize(
    name: &'static str,
    default: usize,
    minimum: usize,
    maximum: usize,
) -> Result<usize, LearningDeckArtifactError> {
    let value = env::var(name)
        .ok()
        .map_or(Ok(default), |value| value.parse::<usize>())
        .map_err(|_| LearningDeckArtifactError::InvalidValue(name))?;
    if !(minimum..=maximum).contains(&value) {
        return Err(LearningDeckArtifactError::InvalidValue(name));
    }
    Ok(value)
}

fn bounded_u64(
    name: &'static str,
    default: u64,
    minimum: u64,
    maximum: u64,
) -> Result<u64, LearningDeckArtifactError> {
    let value = env::var(name)
        .ok()
        .map_or(Ok(default), |value| value.parse::<u64>())
        .map_err(|_| LearningDeckArtifactError::InvalidValue(name))?;
    if !(minimum..=maximum).contains(&value) {
        return Err(LearningDeckArtifactError::InvalidValue(name));
    }
    Ok(value)
}

pub(super) fn guess_content_type(path: &str) -> String {
    match Path::new(path)
        .extension()
        .and_then(|extension| extension.to_str())
        .map(str::to_ascii_lowercase)
        .as_deref()
    {
        Some("css") => "text/css; charset=utf-8",
        Some("js" | "mjs") => "text/javascript; charset=utf-8",
        Some("json") => "application/json",
        Some("svg") => "image/svg+xml",
        Some("png") => "image/png",
        Some("jpg" | "jpeg") => "image/jpeg",
        Some("gif") => "image/gif",
        Some("webp") => "image/webp",
        Some("woff") => "font/woff",
        Some("woff2") => "font/woff2",
        Some("mp4") => "video/mp4",
        Some("webm") => "video/webm",
        _ => "application/octet-stream",
    }
    .to_owned()
}

#[derive(Debug, Error)]
pub(super) enum LearningDeckArtifactError {
    #[error("missing required object-storage setting {0}")]
    Missing(&'static str),
    #[error("unsupported CONTENT_BODY_STORAGE_PROVIDER {0:?}")]
    UnsupportedProvider(String),
    #[error(
        "CONTENT_BODY_STORAGE_ACCESS_KEY and CONTENT_BODY_STORAGE_SECRET_KEY must be set together"
    )]
    IncompleteCredentials,
    #[error("invalid Learning Deck worker configuration in {0}")]
    InvalidValue(&'static str),
    #[error("invalid URL for {0}: {1:?}")]
    InvalidUrl(&'static str, String),
    #[error("unsafe local artifact root {0:?}")]
    UnsafeRoot(PathBuf),
    #[error("unsafe local artifact path {0:?}")]
    UnsafeLocalPath(PathBuf),
    #[error("unsafe Learning Deck object key {0:?}")]
    UnsafeObjectKey(String),
    #[error("unsafe Learning Deck asset path {0:?}")]
    UnsafeAssetPath(PathBuf),
    #[error("Learning Deck artifact contract failed: {0}")]
    Contract(String),
    #[error("Learning Deck artifact contract failed: {}", .0.join("; "))]
    RepairableContract(Vec<String>),
    #[error("stored Learning Deck artifact is unavailable: {0}")]
    ObjectUnavailable(String),
    #[error("Learning Deck object-store operation failed: {0}")]
    ObjectStore(String),
    #[error("Learning Deck local artifact operation failed for {path:?}")]
    File {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("Learning Deck object is not valid UTF-8")]
    Utf8(#[from] std::string::FromUtf8Error),
    #[error("Learning Deck artifact JSON is invalid")]
    Json(#[from] serde_json::Error),
    #[error("Learning Deck artifact validator could not compile")]
    Regex(#[from] regex::Error),
    #[error("could not resolve the current directory")]
    CurrentDirectory(#[from] std::io::Error),
}

impl LearningDeckArtifactError {
    pub(super) const fn repairable(&self) -> bool {
        matches!(self, Self::RepairableContract(_))
    }
}
