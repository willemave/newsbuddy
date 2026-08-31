use std::collections::BTreeMap;
use std::fs;
use std::future::Future;
use std::io::Write as _;
use std::path::{Component, Path, PathBuf};

use newsly_contracts::{AgentLibraryFileResponse, AgentLibraryManifestResponse};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use tempfile::NamedTempFile;
use thiserror::Error;

pub const LIBRARY_MANIFEST_FILENAME: &str = ".newsbuddy-manifest.json";
pub const LEGACY_LIBRARY_MANIFEST_FILENAME: &str = ".newsly-agent-manifest.json";

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct LibrarySyncReceipt {
    pub library_root: PathBuf,
    pub downloaded: usize,
    pub deleted: usize,
    pub unchanged: usize,
    pub repaired: usize,
    pub document_count: usize,
}

#[derive(Debug, Error)]
pub enum LibrarySyncError<E> {
    #[error("missing library root")]
    MissingRoot,
    #[error(
        "remote library manifest is empty; refusing to delete all tracked files without --allow-prune-all"
    )]
    PruneAllRefused,
    #[error("library path escapes the sync root")]
    EscapingPath,
    #[error("refusing to write through symlink in library path: {0}")]
    Symlink(PathBuf),
    #[error("downloaded checksum mismatch for {0}")]
    ChecksumMismatch(String),
    #[error("library transport failed: {0}")]
    Remote(E),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
    #[error("failed to persist atomic library file: {0}")]
    Persist(String),
}

#[derive(Debug, Default, Deserialize, Serialize)]
struct LocalLibraryManifest {
    #[serde(default)]
    files: BTreeMap<String, String>,
}

/// Apply a remote library manifest to a local directory.
///
/// The caller owns HTTP transport. A file is fetched only when it is new, changed, missing, or
/// locally corrupt, keeping the filesystem safety policy independently testable.
///
/// # Errors
///
/// Returns an error for unsafe paths or symlinks, invalid manifests or checksums, local filesystem
/// failures, remote fetch failures, or an unapproved full prune.
pub async fn sync_library<F, Fut, E>(
    root: &Path,
    remote: &AgentLibraryManifestResponse,
    allow_prune_all: bool,
    mut fetch_file: F,
) -> Result<LibrarySyncReceipt, LibrarySyncError<E>>
where
    F: FnMut(String) -> Fut,
    Fut: Future<Output = Result<AgentLibraryFileResponse, E>>,
{
    if root.as_os_str().is_empty() {
        return Err(LibrarySyncError::MissingRoot);
    }
    fs::create_dir_all(root)?;
    set_directory_mode(root)?;

    let canonical_manifest_path = root.join(LIBRARY_MANIFEST_FILENAME);
    let legacy_manifest_path = root.join(LEGACY_LIBRARY_MANIFEST_FILENAME);
    let local = load_local_manifest(&canonical_manifest_path, &legacy_manifest_path)?;
    if remote.documents.is_empty() && !local.files.is_empty() && !allow_prune_all {
        return Err(LibrarySyncError::PruneAllRefused);
    }

    let mut downloaded = 0;
    let mut unchanged = 0;
    let mut repaired = 0;
    let mut desired = BTreeMap::new();

    for document in &remote.documents {
        desired.insert(
            document.relative_path.clone(),
            document.checksum_sha256.clone(),
        );
        let target = safe_library_path(root, &document.relative_path)?;
        if local.files.get(&document.relative_path) == Some(&document.checksum_sha256) {
            if matches!(
                checksum_file(&target),
                Ok(actual) if actual == document.checksum_sha256
            ) {
                unchanged += 1;
                continue;
            }
            repaired += 1;
        }

        let payload = fetch_file(document.relative_path.clone())
            .await
            .map_err(LibrarySyncError::Remote)?;
        if checksum_bytes(payload.text.as_bytes()) != document.checksum_sha256 {
            return Err(LibrarySyncError::ChecksumMismatch(
                document.relative_path.clone(),
            ));
        }
        reject_library_symlinks(root, &target)?;
        let parent = target.parent().ok_or(LibrarySyncError::EscapingPath)?;
        fs::create_dir_all(parent)?;
        set_directory_tree_mode(root, parent)?;
        write_file_atomic(&target, payload.text.as_bytes(), 0o600)?;
        downloaded += 1;
    }

    let mut deleted = 0;
    for relative_path in local.files.keys() {
        if desired.contains_key(relative_path) {
            continue;
        }
        let target = safe_library_path(root, relative_path)?;
        reject_library_symlinks(root, &target)?;
        match fs::remove_file(&target) {
            Ok(()) => {
                if let Some(parent) = target.parent() {
                    prune_empty_library_dirs(parent, root)?;
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
        deleted += 1;
    }

    save_local_manifest(&canonical_manifest_path, &desired)?;
    Ok(LibrarySyncReceipt {
        library_root: root.to_path_buf(),
        downloaded,
        deleted,
        unchanged,
        repaired,
        document_count: remote.documents.len(),
    })
}

fn load_local_manifest(
    canonical_path: &Path,
    legacy_path: &Path,
) -> Result<LocalLibraryManifest, std::io::Error> {
    let data = match fs::read(canonical_path) {
        Ok(data) => data,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => match fs::read(legacy_path) {
            Ok(data) => data,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Ok(LocalLibraryManifest::default());
            }
            Err(error) => return Err(error),
        },
        Err(error) => return Err(error),
    };
    serde_json::from_slice(&data).map_err(std::io::Error::other)
}

fn save_local_manifest<E>(
    path: &Path,
    files: &BTreeMap<String, String>,
) -> Result<(), LibrarySyncError<E>> {
    let mut payload = serde_json::to_vec_pretty(&LocalLibraryManifest {
        files: files.clone(),
    })?;
    payload.push(b'\n');
    write_file_atomic(path, &payload, 0o600)
}

fn safe_library_path<E>(root: &Path, relative_path: &str) -> Result<PathBuf, LibrarySyncError<E>> {
    let relative = Path::new(relative_path);
    if relative.is_absolute()
        || relative.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::RootDir | Component::Prefix(_)
            )
        })
    {
        return Err(LibrarySyncError::EscapingPath);
    }
    Ok(root.join(relative))
}

fn reject_library_symlinks<E>(root: &Path, target: &Path) -> Result<(), LibrarySyncError<E>> {
    let relative = target
        .strip_prefix(root)
        .map_err(|_| LibrarySyncError::EscapingPath)?;
    let mut current = root.to_path_buf();
    for component in relative.components() {
        let Component::Normal(part) = component else {
            continue;
        };
        current.push(part);
        match fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                return Err(LibrarySyncError::Symlink(current));
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
    }
    Ok(())
}

fn checksum_file(path: &Path) -> Result<String, std::io::Error> {
    fs::read(path).map(|bytes| checksum_bytes(&bytes))
}

fn checksum_bytes(bytes: &[u8]) -> String {
    use std::fmt::Write as _;

    Sha256::digest(bytes)
        .iter()
        .fold(String::with_capacity(64), |mut output, byte| {
            let _ = write!(output, "{byte:02x}");
            output
        })
}

fn write_file_atomic<E>(path: &Path, data: &[u8], mode: u32) -> Result<(), LibrarySyncError<E>> {
    let parent = path.parent().ok_or(LibrarySyncError::EscapingPath)?;
    fs::create_dir_all(parent)?;
    let mut temporary = NamedTempFile::new_in(parent)?;
    temporary.write_all(data)?;
    temporary.as_file().sync_all()?;
    set_file_mode(temporary.path(), mode)?;
    temporary
        .persist(path)
        .map_err(|error| LibrarySyncError::Persist(error.error.to_string()))?;
    Ok(())
}

fn prune_empty_library_dirs<E>(start: &Path, stop: &Path) -> Result<(), LibrarySyncError<E>> {
    let mut current = start.to_path_buf();
    while current != stop {
        match fs::remove_dir(&current) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) if error.kind() == std::io::ErrorKind::DirectoryNotEmpty => return Ok(()),
            Err(error) => return Err(error.into()),
        }
        let Some(parent) = current.parent() else {
            break;
        };
        current = parent.to_path_buf();
    }
    Ok(())
}

fn set_directory_tree_mode<E>(root: &Path, leaf: &Path) -> Result<(), LibrarySyncError<E>> {
    let relative = leaf
        .strip_prefix(root)
        .map_err(|_| LibrarySyncError::EscapingPath)?;
    let mut current = root.to_path_buf();
    set_directory_mode(&current)?;
    for component in relative.components() {
        if let Component::Normal(part) = component {
            current.push(part);
            set_directory_mode(&current)?;
        }
    }
    Ok(())
}

#[cfg(unix)]
fn set_directory_mode(path: &Path) -> Result<(), std::io::Error> {
    use std::os::unix::fs::PermissionsExt as _;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
}

#[cfg(not(unix))]
fn set_directory_mode(_path: &Path) -> Result<(), std::io::Error> {
    Ok(())
}

#[cfg(unix)]
fn set_file_mode(path: &Path, mode: u32) -> Result<(), std::io::Error> {
    use std::os::unix::fs::PermissionsExt as _;
    fs::set_permissions(path, fs::Permissions::from_mode(mode))
}

#[cfg(not(unix))]
fn set_file_mode(_path: &Path, _mode: u32) -> Result<(), std::io::Error> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::convert::Infallible;

    use serde_json::json;
    use tempfile::tempdir;

    use super::*;

    fn remote_manifest(path: Option<&str>, text: &str) -> AgentLibraryManifestResponse {
        let documents = path.map_or_else(Vec::new, |path| {
            vec![json!({
                "relative_path": path,
                "content_id": 1,
                "variant": "summary",
                "updated_at": "2026-08-31T12:00:00Z",
                "size_bytes": text.len(),
                "checksum_sha256": checksum_bytes(text.as_bytes())
            })]
        });
        serde_json::from_value(json!({
            "generated_at": "2026-08-31T12:00:00Z",
            "include_source": true,
            "documents": documents
        }))
        .unwrap()
    }

    fn file_payload(path: &str, text: &str) -> AgentLibraryFileResponse {
        serde_json::from_value(json!({
            "relative_path": path,
            "content_id": 1,
            "variant": "summary",
            "updated_at": "2026-08-31T12:00:00Z",
            "checksum_sha256": checksum_bytes(text.as_bytes()),
            "text": text
        }))
        .unwrap()
    }

    #[tokio::test]
    async fn downloads_then_leaves_a_verified_file_unchanged() {
        let directory = tempdir().unwrap();
        let path = "article/example.md";
        let text = "# Example\n";
        let remote = remote_manifest(Some(path), text);
        let first = sync_library(directory.path(), &remote, false, |requested| async move {
            Ok::<_, Infallible>(file_payload(&requested, text))
        })
        .await
        .unwrap();
        assert_eq!(first.downloaded, 1);
        assert_eq!(
            fs::read_to_string(directory.path().join(path)).unwrap(),
            text
        );

        let second = sync_library(directory.path(), &remote, false, |_| async {
            panic!("unchanged file must not be fetched");
            #[allow(unreachable_code)]
            Ok::<AgentLibraryFileResponse, Infallible>(file_payload(path, text))
        })
        .await
        .unwrap();
        assert_eq!(second.unchanged, 1);
        assert_eq!(second.downloaded, 0);
    }

    #[tokio::test]
    async fn repairs_a_corrupt_tracked_file() {
        let directory = tempdir().unwrap();
        let path = "article/example.md";
        let text = "# Correct\n";
        let remote = remote_manifest(Some(path), text);
        sync_library(directory.path(), &remote, false, |requested| async move {
            Ok::<_, Infallible>(file_payload(&requested, text))
        })
        .await
        .unwrap();
        fs::write(directory.path().join(path), "corrupt").unwrap();

        let receipt = sync_library(directory.path(), &remote, false, |requested| async move {
            Ok::<_, Infallible>(file_payload(&requested, text))
        })
        .await
        .unwrap();
        assert_eq!(receipt.repaired, 1);
        assert_eq!(receipt.downloaded, 1);
    }

    #[tokio::test]
    async fn empty_remote_requires_explicit_prune_permission() {
        let directory = tempdir().unwrap();
        let path = "article/example.md";
        fs::create_dir_all(directory.path().join("article")).unwrap();
        fs::write(directory.path().join(path), "old").unwrap();
        fs::write(
            directory.path().join(LIBRARY_MANIFEST_FILENAME),
            format!("{{\"files\":{{\"{path}\":\"old\"}}}}"),
        )
        .unwrap();
        let error = sync_library(
            directory.path(),
            &remote_manifest(None, ""),
            false,
            |_| async {
                panic!("empty manifest has no downloads");
                #[allow(unreachable_code)]
                Ok::<AgentLibraryFileResponse, Infallible>(file_payload(path, ""))
            },
        )
        .await
        .unwrap_err();
        assert!(matches!(error, LibrarySyncError::PruneAllRefused));
        assert!(directory.path().join(path).exists());
    }

    #[tokio::test]
    async fn rejects_escaping_paths_before_fetching() {
        let directory = tempdir().unwrap();
        let remote = remote_manifest(Some("../escape.md"), "bad");
        let error = sync_library(directory.path(), &remote, false, |_| async {
            panic!("unsafe paths must not be fetched");
            #[allow(unreachable_code)]
            Ok::<AgentLibraryFileResponse, Infallible>(file_payload("unused", ""))
        })
        .await
        .unwrap_err();
        assert!(matches!(error, LibrarySyncError::EscapingPath));
    }

    #[tokio::test]
    async fn rejects_a_downloaded_checksum_mismatch() {
        let directory = tempdir().unwrap();
        let path = "article/example.md";
        let remote = remote_manifest(Some(path), "expected");
        let error = sync_library(directory.path(), &remote, false, |requested| async move {
            Ok::<_, Infallible>(file_payload(&requested, "different"))
        })
        .await
        .unwrap_err();
        assert!(matches!(error, LibrarySyncError::ChecksumMismatch(value) if value == path));
        assert!(!directory.path().join(path).exists());
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn rejects_writes_through_symlinks() {
        use std::os::unix::fs::symlink;

        let directory = tempdir().unwrap();
        let outside = tempdir().unwrap();
        symlink(outside.path(), directory.path().join("article")).unwrap();
        let path = "article/example.md";
        let remote = remote_manifest(Some(path), "bad");
        let error = sync_library(directory.path(), &remote, false, |requested| async move {
            Ok::<_, Infallible>(file_payload(&requested, "bad"))
        })
        .await
        .unwrap_err();
        assert!(matches!(error, LibrarySyncError::Symlink(_)));
        assert!(!outside.path().join("example.md").exists());
    }
}
