use std::fs::{self, File};
use std::io::{Seek, SeekFrom, Write};
use std::path::{Component, Path, PathBuf};

use flate2::{Compression, GzBuilder};
use futures_util::stream;
use newsly_db::PreparedAgentCorpusTransfer;
use newsly_e2b::{BoxByteStream, CorpusTransfer, E2bError, MAX_CORPUS_ARCHIVE_BYTES};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use tar::{Builder, Header};
use tempfile::{NamedTempFile, TempPath};
use thiserror::Error;
use tokio::io::AsyncReadExt;

const STREAM_CHUNK_BYTES: usize = 64 * 1024;
const CORPUS_FILE_MODE: u32 = 0o444;
const MAX_MANIFEST_BYTES: u64 = 64 * 1024;

#[derive(Debug)]
pub(super) struct MaterializedAgentCorpusArchive {
    path: TempPath,
    transfer: CorpusTransfer,
}

impl MaterializedAgentCorpusArchive {
    #[must_use]
    pub(super) const fn transfer(&self) -> CorpusTransfer {
        self.transfer
    }

    pub(super) async fn into_stream(self) -> Result<BoxByteStream, AgentCorpusArchiveError> {
        let file = tokio::fs::File::open(&self.path).await?;
        let state = (file, self.path);
        Ok(Box::pin(stream::try_unfold(
            state,
            |(mut file, path)| async move {
                let mut bytes = vec![0; STREAM_CHUNK_BYTES];
                let count = file.read(&mut bytes).await.map_err(|error| {
                    E2bError::Protocol(format!("unable to stream prepared corpus archive: {error}"))
                })?;
                if count == 0 {
                    return Ok(None);
                }
                bytes.truncate(count);
                Ok(Some((bytes.into(), (file, path))))
            },
        )))
    }
}

/// Builds the exact archive consumed by `newsly-vm-bootstrap` after the repository transaction has
/// committed. Every file is opened without following a final-component symlink, checked against
/// its ledger size and SHA-256, and appended from that same open file descriptor.
pub(super) async fn materialize_agent_corpus_archive(
    mirror_root: PathBuf,
    prepared: PreparedAgentCorpusTransfer,
) -> Result<MaterializedAgentCorpusArchive, AgentCorpusArchiveError> {
    tokio::task::spawn_blocking(move || materialize_sync(&mirror_root, &prepared))
        .await
        .map_err(|error| AgentCorpusArchiveError::Join(error.to_string()))?
}

fn materialize_sync(
    mirror_root: &Path,
    prepared: &PreparedAgentCorpusTransfer,
) -> Result<MaterializedAgentCorpusArchive, AgentCorpusArchiveError> {
    let mirror_root = validated_root(mirror_root)?;
    let user_root = mirror_root.join(prepared.user_id.to_string());
    if user_root == mirror_root || !user_root.starts_with(&mirror_root) {
        return Err(AgentCorpusArchiveError::UnsafeMirrorRoot(user_root));
    }
    let canonical_user_root =
        fs::canonicalize(&user_root).map_err(|error| AgentCorpusArchiveError::CorpusFile {
            path: user_root.display().to_string(),
            message: error.to_string(),
        })?;
    if !canonical_user_root.is_dir() {
        return Err(AgentCorpusArchiveError::UnsafeMirrorRoot(
            canonical_user_root,
        ));
    }

    let temporary = NamedTempFile::new()?;
    let encoder = GzBuilder::new()
        .mtime(0)
        .write(temporary, Compression::default());
    let mut archive = Builder::new(encoder);
    archive.mode(tar::HeaderMode::Deterministic);

    let transfer_json = json!({
        "version": 1,
        "user_id": prepared.user_id,
        "from_revision": prepared.from_revision,
        "to_revision": prepared.to_revision,
        "full": prepared.full,
    });
    append_json(&mut archive, "transfer.json", &transfer_json)?;
    append_json(
        &mut archive,
        "manifest.json",
        &json!({
            "version": 2,
            "user_id": prepared.user_id,
            "revision": prepared.to_revision,
            "generated_at": prepared.generated_at.to_rfc3339(),
            "file_count": prepared.total_file_count,
            "complete": host_manifest_complete(&user_root, prepared),
        }),
    )?;
    append_json(
        &mut archive,
        "deletions.json",
        &Value::Array(
            prepared
                .deleted_paths
                .iter()
                .cloned()
                .map(Value::String)
                .collect(),
        ),
    )?;

    let index_name = if prepared.full {
        "index_full.jsonl"
    } else {
        "index_upserts.jsonl"
    };
    let mut index = Vec::new();
    for file in &prepared.active_files {
        serde_json::to_writer(&mut index, &Value::Object(file.index_record.clone()))?;
        index.push(b'\n');
    }
    append_bytes(&mut archive, index_name, &index)?;

    for ledger in &prepared.active_files {
        let relative = validated_relative_path(&ledger.path)?;
        let target = user_root.join(&relative);
        if !target.starts_with(&user_root) || target == user_root {
            return Err(AgentCorpusArchiveError::UnsafeCorpusPath(
                ledger.path.clone(),
            ));
        }
        let metadata =
            fs::symlink_metadata(&target).map_err(|error| AgentCorpusArchiveError::CorpusFile {
                path: ledger.path.clone(),
                message: error.to_string(),
            })?;
        if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
            return Err(AgentCorpusArchiveError::CorpusFile {
                path: ledger.path.clone(),
                message: "ledger target is not a regular non-symlink file".to_owned(),
            });
        }
        let canonical_target =
            fs::canonicalize(&target).map_err(|error| AgentCorpusArchiveError::CorpusFile {
                path: ledger.path.clone(),
                message: error.to_string(),
            })?;
        if !canonical_target.starts_with(&canonical_user_root)
            || canonical_target == canonical_user_root
        {
            return Err(AgentCorpusArchiveError::UnsafeCorpusPath(
                ledger.path.clone(),
            ));
        }
        if metadata.len() != ledger.byte_size {
            return Err(AgentCorpusArchiveError::CorpusChanged {
                path: ledger.path.clone(),
            });
        }
        let mut source = File::open(&target)?;
        let mut hasher = Sha256::new();
        let copied = std::io::copy(
            &mut std::io::Read::by_ref(&mut source),
            &mut HashWriter(&mut hasher),
        )?;
        if copied != ledger.byte_size
            || hex_encode(&hasher.finalize()) != ledger.checksum_sha256.to_ascii_lowercase()
        {
            return Err(AgentCorpusArchiveError::CorpusChanged {
                path: ledger.path.clone(),
            });
        }
        source.seek(SeekFrom::Start(0))?;
        let mut header = regular_header(ledger.byte_size);
        archive.append_data(&mut header, format!("files/{}", ledger.path), source)?;
    }

    let encoder = archive.into_inner()?;
    let temporary = encoder.finish()?;
    temporary.as_file().sync_all()?;
    let archive_bytes = temporary.as_file().metadata()?.len();
    if archive_bytes == 0 || archive_bytes > MAX_CORPUS_ARCHIVE_BYTES {
        return Err(AgentCorpusArchiveError::ArchiveSize {
            observed: archive_bytes,
            maximum: MAX_CORPUS_ARCHIVE_BYTES,
        });
    }
    let path = temporary.into_temp_path();
    Ok(MaterializedAgentCorpusArchive {
        path,
        transfer: CorpusTransfer {
            user_id: u64::try_from(prepared.user_id)
                .map_err(|_| AgentCorpusArchiveError::InvalidUserId(prepared.user_id))?,
            from_revision: prepared.from_revision,
            to_revision: prepared.to_revision,
            full: prepared.full,
            changed_file_count: u32::try_from(prepared.active_files.len())
                .map_err(|_| AgentCorpusArchiveError::CountOverflow("changed files"))?,
            deleted_path_count: u32::try_from(prepared.deleted_paths.len())
                .map_err(|_| AgentCorpusArchiveError::CountOverflow("deleted paths"))?,
            archive_bytes,
        },
    })
}

struct HashWriter<'a>(&'a mut Sha256);

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        encoded.push(char::from(HEX[usize::from(byte >> 4)]));
        encoded.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    encoded
}

impl Write for HashWriter<'_> {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.0.update(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

fn host_manifest_complete(user_root: &Path, prepared: &PreparedAgentCorpusTransfer) -> bool {
    let path = user_root.join("manifest.json");
    let Ok(metadata) = fs::symlink_metadata(&path) else {
        return false;
    };
    if !metadata.file_type().is_file()
        || metadata.file_type().is_symlink()
        || metadata.len() > MAX_MANIFEST_BYTES
    {
        return false;
    }
    let Ok(bytes) = fs::read(path) else {
        return false;
    };
    let Ok(Value::Object(manifest)) = serde_json::from_slice::<Value>(&bytes) else {
        return false;
    };
    manifest.get("complete").and_then(Value::as_bool) == Some(true)
        && manifest.get("user_id").and_then(Value::as_i64) == Some(prepared.user_id)
        && manifest.get("revision").and_then(Value::as_u64) == Some(prepared.to_revision)
}

fn append_json<W: Write>(
    archive: &mut Builder<W>,
    path: &str,
    value: &Value,
) -> Result<(), AgentCorpusArchiveError> {
    let mut bytes = serde_json::to_vec(value)?;
    bytes.push(b'\n');
    append_bytes(archive, path, &bytes)
}

fn append_bytes<W: Write>(
    archive: &mut Builder<W>,
    path: &str,
    bytes: &[u8],
) -> Result<(), AgentCorpusArchiveError> {
    let size = u64::try_from(bytes.len())
        .map_err(|_| AgentCorpusArchiveError::CountOverflow("archive member bytes"))?;
    let mut header = regular_header(size);
    archive.append_data(&mut header, path, bytes)?;
    Ok(())
}

fn regular_header(size: u64) -> Header {
    let mut header = Header::new_gnu();
    header.set_entry_type(tar::EntryType::Regular);
    header.set_size(size);
    header.set_mode(CORPUS_FILE_MODE);
    header.set_uid(0);
    header.set_gid(0);
    header.set_mtime(0);
    header.set_cksum();
    header
}

fn validated_root(path: &Path) -> Result<PathBuf, AgentCorpusArchiveError> {
    if !path.is_absolute()
        || path == Path::new("/")
        || path.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::CurDir | Component::Prefix(_)
            )
        })
    {
        return Err(AgentCorpusArchiveError::UnsafeMirrorRoot(
            path.to_path_buf(),
        ));
    }
    Ok(path.to_path_buf())
}

fn validated_relative_path(value: &str) -> Result<PathBuf, AgentCorpusArchiveError> {
    let path = Path::new(value);
    if path.is_absolute()
        || value.contains('\\')
        || path.components().any(|component| {
            matches!(
                component,
                Component::ParentDir
                    | Component::CurDir
                    | Component::RootDir
                    | Component::Prefix(_)
            )
        })
        || path
            .components()
            .next()
            .is_some_and(|component| component.as_os_str() == "workspace")
    {
        return Err(AgentCorpusArchiveError::UnsafeCorpusPath(value.to_owned()));
    }
    Ok(path.to_path_buf())
}

#[derive(Debug, Error)]
pub enum AgentCorpusArchiveError {
    #[error("agent corpus mirror root is unsafe: {0}")]
    UnsafeMirrorRoot(PathBuf),
    #[error("agent corpus path is unsafe: {0}")]
    UnsafeCorpusPath(String),
    #[error("agent corpus file {path} is invalid: {message}")]
    CorpusFile { path: String, message: String },
    #[error("agent corpus file changed after its database snapshot: {path}")]
    CorpusChanged { path: String },
    #[error("agent corpus archive is {observed} bytes; expected 1-{maximum}")]
    ArchiveSize { observed: u64, maximum: u64 },
    #[error("agent corpus user id is invalid: {0}")]
    InvalidUserId(i64),
    #[error("agent corpus {0} count is too large")]
    CountOverflow(&'static str),
    #[error("agent corpus archive task failed: {0}")]
    Join(String),
    #[error("agent corpus archive I/O failed")]
    Io(#[from] std::io::Error),
    #[error("agent corpus archive JSON encoding failed")]
    Json(#[from] serde_json::Error),
}
