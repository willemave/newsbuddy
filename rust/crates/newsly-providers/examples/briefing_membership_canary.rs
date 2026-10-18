//! Opt-in two-call membership canary using captured artifacts; never accesses the database.
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use newsly_providers::{BriefingCompositionGateway, BriefingCompositionSource};
use serde::Deserialize;
use serde_json::json;

#[derive(Deserialize)]
struct CapturedArtifact {
    segments: Vec<CapturedSegment>,
    sources: Vec<CapturedSource>,
}

#[derive(Deserialize)]
struct CapturedSegment {
    source_keys: Vec<String>,
}

#[derive(Deserialize)]
struct CapturedSource {
    source_key: String,
    kind: Option<String>,
    id: i64,
    title: String,
    publisher: Option<String>,
    source_name: Option<String>,
    summary: Option<String>,
    key_points: Option<Vec<String>>,
    url: Option<String>,
    image_url: Option<String>,
    thumbnail_url: Option<String>,
    published_at: Option<String>,
    content_type: Option<String>,
    briefing_context: Option<String>,
}

impl From<CapturedSource> for BriefingCompositionSource {
    fn from(source: CapturedSource) -> Self {
        Self {
            source_key: source.source_key,
            kind: source
                .kind
                .or(source.content_type)
                .unwrap_or_else(|| "news".to_owned()),
            id: source.id,
            title: source.title,
            source_name: source.publisher.or(source.source_name),
            summary: source.summary,
            key_points: source.key_points.unwrap_or_default(),
            url: source.url,
            image_url: source.image_url,
            thumbnail_url: source.thumbnail_url,
            published_at: source.published_at,
            briefing_context: source.briefing_context,
        }
    }
}

fn load_sources(path: &Path) -> Result<Vec<BriefingCompositionSource>, Box<dyn std::error::Error>> {
    let artifact: CapturedArtifact = serde_json::from_slice(&std::fs::read(path)?)?;
    let segment = artifact
        .segments
        .first()
        .ok_or("artifact has no first segment")?;
    let keys = segment
        .source_keys
        .iter()
        .map(String::as_str)
        .collect::<BTreeSet<_>>();
    if keys.len() != 4 || segment.source_keys.len() != 4 {
        return Err("first segment must name exactly four unique sources".into());
    }
    let mut matching = BTreeMap::new();
    for source in artifact.sources {
        if keys.contains(source.source_key.as_str()) {
            let key = source.source_key.clone();
            if matching.insert(key, source).is_some() {
                return Err("artifact repeats a source from the first segment".into());
            }
        }
    }
    segment
        .source_keys
        .iter()
        .map(|key| {
            matching
                .remove(key)
                .map(BriefingCompositionSource::from)
                .ok_or_else(|| format!("artifact is missing first-segment source {key:?}").into())
        })
        .collect()
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let paths = std::env::args_os().skip(1).collect::<Vec<_>>();
    if paths.len() != 2 {
        return Err("exactly two captured artifact JSON paths required".into());
    }
    // Validate both captures before spending on either of the two authorized calls.
    let inputs = paths
        .iter()
        .map(|path| load_sources(Path::new(path)))
        .collect::<Result<Vec<_>, _>>()?;
    let gateway = BriefingCompositionGateway::from_env()?;
    let mut observations = Vec::new();
    for (path, sources) in paths.iter().zip(inputs) {
        let started = std::time::Instant::now();
        // Exactly one attempt for each capture, sequentially, with no correction or judge call.
        let result = gateway.name_lens(&sources, 3).await;
        let observation = match result {
            Ok(generated) => json!({
                "artifact": path.to_string_lossy(),
                "attempts": 1,
                "elapsed_ms": started.elapsed().as_millis(),
                "provider_succeeded": true,
                "source_keys": sources.iter().map(|source| &source.source_key).collect::<Vec<_>>(),
                "name": generated.name,
                "model": generated.model,
                "usage": generated.usage,
                "provider_response_id": generated.provider_response_id,
            }),
            Err(error) => json!({
                "artifact": path.to_string_lossy(),
                "attempts": 1,
                "elapsed_ms": started.elapsed().as_millis(),
                "provider_succeeded": false,
                "model": gateway.naming_model_spec(),
                "usage": error.observed_usage(),
                "rejection_code": error.reason_code(),
                "error": error.to_string(),
            }),
        };
        observations.push(observation);
    }
    println!("{}", serde_json::to_string_pretty(&observations)?);
    Ok(())
}
