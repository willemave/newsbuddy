//! Capture complete candidates before validation and restore only bounded, verified bytes.
use std::collections::BTreeSet;
use std::path::{Component, Path};

use serde::Deserialize;
use serde_json::Map;

use super::LearningDeckAgentError;
use crate::learning_deck::artifacts::{
    LearningDeckArtifactLimits, LearningDeckAsset, OUTPUT_INDEX_HTML, OUTPUT_SOURCE_METADATA,
    OUTPUT_SOURCE_NOTES, drafts::DeckCandidate, guess_content_type,
};
use crate::task_tools::TaskToolExecutor;

const METADATA_MAX_BYTES: usize = 1_000_000;
const CANDIDATE_MAX_BYTES: usize = 20 * 1024 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Inventory {
    assets: Vec<String>,
    metadata: bool,
}

pub(super) async fn capture_candidate(
    tools: &TaskToolExecutor,
    limits: &LearningDeckArtifactLimits,
) -> Result<DeckCandidate, LearningDeckAgentError> {
    // lstat rejects symlinks, including the output root and intermediate asset directories.
    // Print a complete JSON inventory or fail; truncation cannot become a partial snapshot.
    let script = inventory_script(limits);
    let raw = run_housekeeping(tools, &format!("python3 -I -c {}", shell_quote(&script))).await?;
    let inventory: Inventory = serde_json::from_str(&raw)
        .map_err(|error| contract_error(&format!("invalid candidate inventory: {error}")))?;
    validate_asset_paths(&inventory.assets, limits.asset_count)?;
    let index_html = tools
        .read_text_typed(OUTPUT_INDEX_HTML, limits.index_html_bytes)
        .await
        .map_err(|error| LearningDeckAgentError::from_file(error, OUTPUT_INDEX_HTML))?;
    let source_notes = tools
        .read_text_typed(OUTPUT_SOURCE_NOTES, limits.source_notes_bytes)
        .await
        .map_err(|error| LearningDeckAgentError::from_file(error, OUTPUT_SOURCE_NOTES))?;
    let source_metadata = if inventory.metadata {
        let raw = tools
            .read_text_typed(OUTPUT_SOURCE_METADATA, METADATA_MAX_BYTES)
            .await
            .map_err(|error| LearningDeckAgentError::from_file(error, OUTPUT_SOURCE_METADATA))?;
        serde_json::from_str(&raw)
            .map_err(|error| contract_error(&format!("invalid candidate metadata: {error}")))?
    } else {
        Map::new()
    };
    let mut assets = Vec::with_capacity(inventory.assets.len());
    let mut total = index_html.len().saturating_add(source_notes.len());
    for relative_path in inventory.assets {
        let path = format!("output/{relative_path}");
        let bytes = tools
            .read_bytes_typed(
                &path,
                limits
                    .asset_bytes
                    .min(CANDIDATE_MAX_BYTES.saturating_sub(total)),
            )
            .await
            .map_err(|error| LearningDeckAgentError::from_file(error, &path))?;
        total = total.saturating_add(bytes.len());
        assets.push(LearningDeckAsset {
            content_type: guess_content_type(&relative_path),
            relative_path,
            bytes,
        });
    }
    let candidate = DeckCandidate {
        index_html,
        source_notes,
        source_metadata,
        assets,
    };
    candidate
        .check_bounds(limits)
        .map_err(|error| contract_error(&error.to_string()))?;
    Ok(candidate)
}

fn inventory_script(limits: &LearningDeckArtifactLimits) -> String {
    let body = format!(
        r"import json, os, stat
from pathlib import Path
root = Path('output')
assert stat.S_ISDIR(root.lstat().st_mode), 'output must be a real directory'
total = 0
for name, limit in [('index.html', {html}), ('source-notes.md', {notes})]:
    info = (root / name).lstat()
    assert stat.S_ISREG(info.st_mode), 'candidate input must be a regular file'
    assert info.st_size <= limit, 'candidate input exceeds size limit'
    total += info.st_size
metadata = root / 'source-metadata.json'
has_metadata = os.path.lexists(metadata)
if has_metadata:
    info = metadata.lstat()
    assert stat.S_ISREG(info.st_mode), 'metadata must be a regular file'
    assert info.st_size <= {metadata}, 'metadata exceeds size limit'
assets = []
asset_root = root / 'assets'
if os.path.lexists(asset_root):
    assert stat.S_ISDIR(asset_root.lstat().st_mode), 'assets must be a real directory'
    def fail_walk(error):
        raise error
    for directory, dirs, files in os.walk(asset_root, followlinks=False, onerror=fail_walk):
        for name in dirs:
            assert stat.S_ISDIR((Path(directory) / name).lstat().st_mode), 'asset directory must not be a symlink'
        for name in files:
            path = Path(directory) / name
            info = path.lstat()
            assert stat.S_ISREG(info.st_mode), 'asset must be a regular file'
            assert info.st_size <= {asset}, 'asset exceeds size limit'
            assets.append(path.relative_to(root).as_posix())
            assert len(assets) <= {count}, 'too many assets'
            total += info.st_size
            assert total <= {total}, 'candidate exceeds aggregate size limit'
print(json.dumps({{'assets': sorted(assets), 'metadata': has_metadata}}))
",
        html = limits.index_html_bytes,
        notes = limits.source_notes_bytes,
        metadata = METADATA_MAX_BYTES,
        asset = limits.asset_bytes,
        count = limits.asset_count,
        total = CANDIDATE_MAX_BYTES,
    );
    let body = body.lines().fold(String::new(), |mut indented, line| {
        indented.push_str("    ");
        indented.push_str(line);
        indented.push('\n');
        indented
    });
    format!(
        "import json, sys\ntry:\n{body}except Exception as error:\n    print(json.dumps({{\"error\": str(error)[:2000]}}))\n    sys.exit(1)\n"
    )
}

pub(super) async fn restore_candidate(
    tools: &TaskToolExecutor,
    limits: &LearningDeckArtifactLimits,
    candidate: &DeckCandidate,
) -> Result<(), LearningDeckAgentError> {
    candidate
        .check_bounds(limits)
        .map_err(|error| contract_error(&error.to_string()))?;
    let paths = candidate
        .assets
        .iter()
        .map(|asset| asset.relative_path.clone())
        .collect::<Vec<_>>();
    validate_asset_paths(&paths, limits.asset_count)?;
    let metadata = serde_json::to_vec(&candidate.source_metadata)?;
    if metadata.len() > METADATA_MAX_BYTES {
        return Err(contract_error("candidate metadata exceeds size limit"));
    }
    // Recreate output so old model files and symlinks cannot survive a restored candidate.
    let mut directories = BTreeSet::from(["output/assets".to_owned()]);
    for path in &paths {
        if let Some(parent) = Path::new(path).parent() {
            directories.insert(format!("output/{}", parent.display()));
        }
    }
    let directories = directories
        .iter()
        .map(|path| shell_quote(path))
        .collect::<Vec<_>>()
        .join(" ");
    run_housekeeping(
        tools,
        &format!("rm -rf -- output && mkdir -p -- {directories}"),
    )
    .await?;
    for (path, bytes) in [
        (OUTPUT_INDEX_HTML, candidate.index_html.as_bytes().to_vec()),
        (
            OUTPUT_SOURCE_NOTES,
            candidate.source_notes.as_bytes().to_vec(),
        ),
        (OUTPUT_SOURCE_METADATA, metadata),
    ] {
        tools
            .write_bytes_typed(path, bytes)
            .await
            .map_err(|error| LearningDeckAgentError::from_file(error, path))?;
    }
    for asset in &candidate.assets {
        let path = format!("output/{}", asset.relative_path);
        tools
            .write_bytes_typed(&path, asset.bytes.clone())
            .await
            .map_err(|error| LearningDeckAgentError::from_file(error, &path))?;
    }
    Ok(())
}

fn validate_asset_paths(paths: &[String], maximum: usize) -> Result<(), LearningDeckAgentError> {
    let mut unique = BTreeSet::new();
    if paths.len() > maximum {
        return Err(contract_error("too many candidate assets"));
    }
    for path in paths {
        if !path.starts_with("assets/")
            || path == "assets/"
            || path.trim() != path
            || path.contains('\\')
            || path.chars().any(char::is_control)
            || path
                .split('/')
                .any(|part| part.is_empty() || part == "." || part == "..")
            || Path::new(path)
                .components()
                .any(|part| !matches!(part, Component::Normal(_)))
            || !unique.insert(path)
        {
            return Err(contract_error("unsafe or duplicate candidate asset path"));
        }
    }
    Ok(())
}

fn contract_error(message: &str) -> LearningDeckAgentError {
    LearningDeckAgentError::Artifact {
        message: message.to_owned(),
        report: Map::new(),
        repairable: true,
    }
}

fn shell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}

async fn run_housekeeping(
    tools: &TaskToolExecutor,
    command: &str,
) -> Result<String, LearningDeckAgentError> {
    let (stdout, exit_code) = tools.run_host_command(command).await.map_err(|error| {
        LearningDeckAgentError::from_file(error, "candidate filesystem command")
    })?;
    if exit_code != 0 {
        let reason = serde_json::from_str::<serde_json::Value>(&stdout)
            .ok()
            .and_then(|value| {
                value
                    .get("error")
                    .and_then(serde_json::Value::as_str)
                    .map(str::to_owned)
            })
            .unwrap_or_else(|| "candidate filesystem inventory or restore failed".to_owned());
        return Err(contract_error(&reason));
    }
    Ok(stdout)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn invalid_generated_candidates_are_repairable() {
        assert!(matches!(
            contract_error("missing index.html"),
            LearningDeckAgentError::Artifact {
                repairable: true,
                ..
            }
        ));
    }

    #[test]
    fn rejects_unsafe_duplicate_and_excessive_paths() {
        for path in [
            "../escape",
            "/assets/a",
            "assets/../a",
            "assets//a",
            "assets/./a",
            "assets/a\n",
            "assets/",
            "assets/a\\b",
        ] {
            assert!(
                validate_asset_paths(&[path.to_owned()], 1).is_err(),
                "{path:?}"
            );
        }
        assert!(validate_asset_paths(&["assets/a".into(), "assets/a".into()], 2).is_err());
        assert!(validate_asset_paths(&["assets/a".into()], 0).is_err());
        assert!(validate_asset_paths(&["assets/nested/it's $(safe).png".into()], 1).is_ok());
    }

    #[test]
    fn inventory_rejects_symlinks_and_size_or_count_overflow() {
        let root = tempfile::tempdir().unwrap();
        let output = root.path().join("output");
        std::fs::create_dir_all(output.join("assets/nested")).unwrap();
        std::fs::write(output.join("index.html"), "html").unwrap();
        std::fs::write(output.join("source-notes.md"), "notes").unwrap();
        std::fs::write(output.join("assets/nested/picture.png"), b"image").unwrap();
        let mut limits = LearningDeckArtifactLimits {
            index_html_bytes: 10,
            source_notes_bytes: 10,
            asset_count: 1,
            asset_bytes: 10,
        };
        let run = |limits: &LearningDeckArtifactLimits| {
            std::process::Command::new("python3")
                .args(["-I", "-c", &inventory_script(limits)])
                .current_dir(root.path())
                .output()
                .unwrap()
        };
        let result = run(&limits);
        assert!(result.status.success(), "{result:?}");
        let inventory: Inventory = serde_json::from_slice(&result.stdout).unwrap();
        assert_eq!(inventory.assets, ["assets/nested/picture.png"]);
        assert!(!inventory.metadata);
        std::fs::remove_file(output.join("source-notes.md")).unwrap();
        let missing = run(&limits);
        assert!(!missing.status.success());
        let diagnostic: serde_json::Value = serde_json::from_slice(&missing.stdout).unwrap();
        assert!(
            diagnostic["error"]
                .as_str()
                .unwrap()
                .contains("source-notes.md")
        );
        std::fs::write(output.join("source-notes.md"), "notes").unwrap();
        limits.asset_count = 0;
        assert!(!run(&limits).status.success());
        limits.asset_count = 1;
        limits.asset_bytes = 1;
        assert!(!run(&limits).status.success());
        limits.asset_bytes = 10;
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink("nested", output.join("assets/link")).unwrap();
            assert!(!run(&limits).status.success());
            std::fs::remove_file(output.join("assets/link")).unwrap();
            std::fs::remove_file(output.join("assets/nested/picture.png")).unwrap();
            std::os::unix::fs::symlink(
                "../../index.html",
                output.join("assets/nested/picture.png"),
            )
            .unwrap();
            assert!(!run(&limits).status.success());
        }
    }

    #[test]
    fn shell_quote_contains_apostrophes_without_executing_metacharacters() {
        assert_eq!(shell_quote("it's $(safe)"), "'it'\\''s $(safe)'");
    }
}
