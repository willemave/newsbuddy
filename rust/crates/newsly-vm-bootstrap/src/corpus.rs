use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};

use flate2::read::GzDecoder;
use serde::Deserialize;
use serde_json::{Map, Value};
use tempfile::{Builder as TempBuilder, NamedTempFile, TempDir};

use crate::error::{BootstrapError, Result};

const VM_DATA_ROOT: &str = "/data";
const WORKSPACE_UID: u32 = 1_000;
const WORKSPACE_GID: u32 = 1_000;
const CORPUS_ROOTS: [&str; 5] = ["knowledge", "content", "news", "briefings", "chats"];
const MAX_COMPRESSED_ARCHIVE_BYTES: u64 = 128 * 1024 * 1024;
const MAX_EXPANDED_ARCHIVE_BYTES: u64 = 512 * 1024 * 1024;
const MAX_ARCHIVE_ENTRIES: usize = 25_000;
const MAX_ARCHIVE_PATH_BYTES: usize = 1_024;
const MAX_DOCUMENT_BYTES: u64 = 2_000_000;
const MAX_TRANSFER_JSON_BYTES: u64 = 64 * 1024;
const MAX_MANIFEST_JSON_BYTES: u64 = 64 * 1024;
const MAX_DELETIONS_JSON_BYTES: u64 = 8 * 1024 * 1024;
const MAX_INDEX_BYTES: u64 = 64 * 1024 * 1024;
const MAX_INDEX_LINE_BYTES: usize = 1024 * 1024;
const MAX_INDEX_RECORDS: usize = 250_000;
const MAX_MANIFEST_FILE_COUNT: u64 = MAX_INDEX_RECORDS as u64;

#[derive(Debug, Deserialize)]
struct TransferMetadata {
    version: u32,
    user_id: u64,
    from_revision: u64,
    to_revision: u64,
    full: bool,
}

#[derive(Debug)]
struct StagedTransfer {
    _directory: TempDir,
    root: PathBuf,
    transfer: TransferMetadata,
    manifest: Value,
    deletions: BTreeSet<String>,
    index_name: &'static str,
    files: BTreeMap<String, PathBuf>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum MemberKind {
    Transfer,
    Manifest,
    Deletions,
    FullIndex,
    DeltaIndex,
    CorpusFile,
}

impl MemberKind {
    const fn size_limit(self) -> u64 {
        match self {
            Self::Transfer => MAX_TRANSFER_JSON_BYTES,
            Self::Manifest => MAX_MANIFEST_JSON_BYTES,
            Self::Deletions => MAX_DELETIONS_JSON_BYTES,
            Self::FullIndex | Self::DeltaIndex => MAX_INDEX_BYTES,
            Self::CorpusFile => MAX_DOCUMENT_BYTES,
        }
    }
}

/// Apply an archive to `/data`, restore workspace ownership, and remove the archive.
pub fn install_vm_archive(archive_path: &Path) -> Result<()> {
    let result = install_archive_at(archive_path, Path::new(VM_DATA_ROOT))
        .and_then(|()| configure_vm_workspace(Path::new(VM_DATA_ROOT)));
    let cleanup = fs::remove_file(archive_path).map_err(|error| {
        BootstrapError::io("removing installed corpus archive", archive_path, error)
    });
    match (result, cleanup) {
        (Ok(()), Ok(())) => Ok(()),
        (Err(error), _) | (Ok(()), Err(error)) => Err(error),
    }
}

/// Validate and apply one full or delta corpus transfer to a chosen data root.
///
/// This lower-level entrypoint intentionally leaves the source archive in place so fixture tests
/// and host-side diagnostics can inspect it. The VM CLI uses [`install_vm_archive`] instead.
pub fn install_archive_at(archive_path: &Path, data_root: &Path) -> Result<()> {
    validate_archive_file(archive_path)?;
    let staged = stage_archive(archive_path)?;
    validate_transfer(&staged)?;
    apply_transfer(&staged, data_root)
}

fn validate_archive_file(archive_path: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(archive_path)
        .map_err(|error| BootstrapError::io("inspecting corpus archive", archive_path, error))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(BootstrapError::InvalidArchive(
            "archive must be a regular file, not a link or special file".to_owned(),
        ));
    }
    if metadata.len() > MAX_COMPRESSED_ARCHIVE_BYTES {
        return Err(BootstrapError::InvalidArchive(format!(
            "compressed archive exceeds {MAX_COMPRESSED_ARCHIVE_BYTES} bytes"
        )));
    }
    Ok(())
}

fn stage_archive(archive_path: &Path) -> Result<StagedTransfer> {
    let directory = TempBuilder::new()
        .prefix("newsly-agent-data-stage-")
        .tempdir()
        .map_err(|error| BootstrapError::io("creating corpus staging directory", "/tmp", error))?;
    let root = directory.path().to_path_buf();
    let (seen, files) = extract_archive_entries(archive_path, &root)?;

    let transfer: TransferMetadata = read_json_file(
        &root.join("transfer.json"),
        MAX_TRANSFER_JSON_BYTES,
        "transfer metadata",
    )?;
    let manifest = read_json_file::<Value>(
        &root.join("manifest.json"),
        MAX_MANIFEST_JSON_BYTES,
        "corpus manifest",
    )?;
    let deletion_values = read_json_file::<Vec<String>>(
        &root.join("deletions.json"),
        MAX_DELETIONS_JSON_BYTES,
        "corpus deletions",
    )?;
    let mut deletions = BTreeSet::new();
    for value in deletion_values {
        validate_corpus_path(&value)?;
        if !deletions.insert(value.clone()) {
            return Err(BootstrapError::InvalidArchive(format!(
                "deletions.json contains duplicate path {value}"
            )));
        }
    }

    let index_name = required_index_name(&transfer, &seen)?;
    for required in [
        "transfer.json",
        "manifest.json",
        "deletions.json",
        index_name,
    ] {
        if !seen.contains(required) {
            return Err(BootstrapError::InvalidArchive(format!(
                "archive is missing required entry {required}"
            )));
        }
    }

    Ok(StagedTransfer {
        _directory: directory,
        root,
        transfer,
        manifest,
        deletions,
        index_name,
        files,
    })
}

fn extract_archive_entries(
    archive_path: &Path,
    root: &Path,
) -> Result<(HashSet<String>, BTreeMap<String, PathBuf>)> {
    let file = File::open(archive_path)
        .map_err(|error| BootstrapError::io("opening corpus archive", archive_path, error))?;
    let decoder = GzDecoder::new(BufReader::new(file));
    let mut archive = tar::Archive::new(decoder);
    let entries = archive.entries().map_err(|error| {
        BootstrapError::InvalidArchive(format!("unable to read tar entries: {error}"))
    })?;

    let mut seen = HashSet::new();
    let mut expanded_bytes = 0_u64;
    let mut entry_count = 0_usize;
    let mut files = BTreeMap::new();
    for entry in entries {
        entry_count += 1;
        if entry_count > MAX_ARCHIVE_ENTRIES {
            return Err(BootstrapError::InvalidArchive(format!(
                "archive contains more than {MAX_ARCHIVE_ENTRIES} entries"
            )));
        }
        let mut entry = entry.map_err(|error| {
            BootstrapError::InvalidArchive(format!("unable to read tar entry: {error}"))
        })?;
        let name = std::str::from_utf8(entry.path_bytes().as_ref())
            .map_err(|_| {
                BootstrapError::InvalidArchive("archive path is not valid UTF-8".to_owned())
            })?
            .to_owned();
        let kind = classify_member(&name)?;
        if !entry.header().entry_type().is_file() {
            return Err(BootstrapError::InvalidArchive(format!(
                "archive entry {name} is not a regular file; links and special files are forbidden"
            )));
        }
        if !seen.insert(name.clone()) {
            return Err(BootstrapError::InvalidArchive(format!(
                "archive contains duplicate entry {name}"
            )));
        }
        let size = entry.size();
        if size > kind.size_limit() {
            return Err(BootstrapError::InvalidArchive(format!(
                "archive entry {name} exceeds its {} byte limit",
                kind.size_limit()
            )));
        }
        expanded_bytes = expanded_bytes.checked_add(size).ok_or_else(|| {
            BootstrapError::InvalidArchive("expanded archive size overflowed".to_owned())
        })?;
        if expanded_bytes > MAX_EXPANDED_ARCHIVE_BYTES {
            return Err(BootstrapError::InvalidArchive(format!(
                "expanded archive exceeds {MAX_EXPANDED_ARCHIVE_BYTES} bytes"
            )));
        }

        let destination = root.join(&name);
        create_stage_parent(root, &destination)?;
        let mut output = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&destination)
            .map_err(|error| {
                BootstrapError::io("creating staged corpus entry", &destination, error)
            })?;
        let copied = std::io::copy(&mut entry, &mut output).map_err(|error| {
            BootstrapError::io("extracting staged corpus entry", &destination, error)
        })?;
        if copied != size {
            return Err(BootstrapError::InvalidArchive(format!(
                "archive entry {name} was truncated"
            )));
        }
        output.sync_all().map_err(|error| {
            BootstrapError::io("syncing staged corpus entry", &destination, error)
        })?;
        if kind == MemberKind::CorpusFile {
            let relative = name
                .strip_prefix("files/")
                .expect("classified corpus member has files prefix")
                .to_owned();
            files.insert(relative, destination);
        }
    }
    Ok((seen, files))
}

fn required_index_name(
    transfer: &TransferMetadata,
    seen: &HashSet<String>,
) -> Result<&'static str> {
    let index_name = if transfer.full {
        if seen.contains("index_upserts.jsonl") {
            return Err(BootstrapError::InvalidArchive(
                "full archive cannot contain index_upserts.jsonl".to_owned(),
            ));
        }
        "index_full.jsonl"
    } else {
        if seen.contains("index_full.jsonl") {
            return Err(BootstrapError::InvalidArchive(
                "delta archive cannot contain index_full.jsonl".to_owned(),
            ));
        }
        "index_upserts.jsonl"
    };
    Ok(index_name)
}

fn classify_member(name: &str) -> Result<MemberKind> {
    validate_member_path(name)?;
    match name {
        "transfer.json" => Ok(MemberKind::Transfer),
        "manifest.json" => Ok(MemberKind::Manifest),
        "deletions.json" => Ok(MemberKind::Deletions),
        "index_full.jsonl" => Ok(MemberKind::FullIndex),
        "index_upserts.jsonl" => Ok(MemberKind::DeltaIndex),
        _ => {
            let relative = name.strip_prefix("files/").ok_or_else(|| {
                BootstrapError::InvalidArchive(format!("unexpected archive entry {name}"))
            })?;
            validate_corpus_path(relative)?;
            Ok(MemberKind::CorpusFile)
        }
    }
}

fn validate_member_path(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > MAX_ARCHIVE_PATH_BYTES
        || value.starts_with('/')
        || value.ends_with('/')
        || value.contains('\\')
        || value.chars().any(char::is_control)
    {
        return Err(BootstrapError::InvalidArchive(format!(
            "unsafe archive path {value:?}"
        )));
    }
    if value
        .split('/')
        .any(|component| component.is_empty() || matches!(component, "." | ".."))
    {
        return Err(BootstrapError::InvalidArchive(format!(
            "unsafe archive path {value:?}"
        )));
    }
    Ok(())
}

fn validate_corpus_path(value: &str) -> Result<()> {
    validate_member_path(value)?;
    let mut components = value.split('/');
    let first = components.next().unwrap_or_default();
    if components.next().is_none() || first == "workspace" || !CORPUS_ROOTS.contains(&first) {
        return Err(BootstrapError::InvalidArchive(format!(
            "unsafe corpus path {value:?}"
        )));
    }
    Ok(())
}

fn create_stage_parent(root: &Path, destination: &Path) -> Result<()> {
    let parent = destination
        .parent()
        .ok_or_else(|| BootstrapError::InvalidArchive("staged entry has no parent".to_owned()))?;
    if !parent.starts_with(root) {
        return Err(BootstrapError::InvalidArchive(
            "staged entry escaped the staging directory".to_owned(),
        ));
    }
    fs::create_dir_all(parent)
        .map_err(|error| BootstrapError::io("creating staged corpus parent", parent, error))
}

fn validate_transfer(staged: &StagedTransfer) -> Result<()> {
    let transfer = &staged.transfer;
    if transfer.version != 1 {
        return Err(BootstrapError::InvalidArchive(format!(
            "unsupported transfer version {}",
            transfer.version
        )));
    }
    if transfer.to_revision < transfer.from_revision {
        return Err(BootstrapError::InvalidArchive(
            "target revision precedes source revision".to_owned(),
        ));
    }
    let manifest = staged.manifest.as_object().ok_or_else(|| {
        BootstrapError::InvalidArchive("manifest.json must contain an object".to_owned())
    })?;
    if require_u64(manifest, "version", "manifest.json")? != 2 {
        return Err(BootstrapError::InvalidArchive(
            "unsupported corpus manifest version".to_owned(),
        ));
    }
    let manifest_user_id = require_u64(manifest, "user_id", "manifest.json")?;
    if manifest_user_id != transfer.user_id {
        return Err(BootstrapError::InvalidArchive(
            "manifest user_id does not match transfer".to_owned(),
        ));
    }
    let manifest_revision = require_u64(manifest, "revision", "manifest.json")?;
    if manifest_revision != transfer.to_revision {
        return Err(BootstrapError::InvalidArchive(
            "manifest revision does not match transfer".to_owned(),
        ));
    }
    let file_count = require_u64(manifest, "file_count", "manifest.json")?;
    if file_count > MAX_MANIFEST_FILE_COUNT {
        return Err(BootstrapError::InvalidArchive(format!(
            "manifest file_count exceeds {MAX_MANIFEST_FILE_COUNT}"
        )));
    }
    if !manifest.get("complete").is_some_and(Value::is_boolean) {
        return Err(BootstrapError::InvalidArchive(
            "manifest complete must be a boolean".to_owned(),
        ));
    }
    Ok(())
}

fn apply_transfer(staged: &StagedTransfer, data_root: &Path) -> Result<()> {
    ensure_directory_without_symlink(data_root)?;
    let workspace = data_root.join("workspace");
    ensure_directory_without_symlink(&workspace)?;

    if !staged.transfer.full {
        validate_current_manifest(staged, data_root)?;
    }

    let mut records = if staged.transfer.full {
        BTreeMap::new()
    } else {
        read_index(&data_root.join("index.jsonl"))?
    };
    for path in &staged.deletions {
        records.remove(path);
    }
    let upserts = read_index(&staged.root.join(staged.index_name))?;
    let upsert_paths = upserts.keys().cloned().collect::<BTreeSet<_>>();
    let file_paths = staged.files.keys().cloned().collect::<BTreeSet<_>>();
    if upsert_paths != file_paths {
        return Err(BootstrapError::InvalidArchive(
            "index upserts and transferred corpus files do not have identical paths".to_owned(),
        ));
    }
    records.extend(upserts);
    let expected_file_count = staged
        .manifest
        .get("file_count")
        .and_then(Value::as_u64)
        .expect("validated manifest file_count");
    if records.len() as u64 != expected_file_count {
        return Err(BootstrapError::InvalidArchive(format!(
            "resulting index has {} records but manifest declares {expected_file_count}",
            records.len()
        )));
    }

    let index_bytes = encode_index(&records)?;
    let mut manifest_bytes = serde_json::to_vec_pretty(&staged.manifest)
        .map_err(|error| BootstrapError::json("encoding corpus manifest", error))?;
    manifest_bytes.push(b'\n');

    // Validate the complete staged transfer and resulting index before mutating `/data`.
    if staged.transfer.full {
        for name in CORPUS_ROOTS
            .into_iter()
            .chain(["index.jsonl", "manifest.json"])
        {
            remove_path_safely(data_root, name)?;
        }
    }
    for path in &staged.deletions {
        remove_path_safely(data_root, path)?;
    }
    for (relative, source) in &staged.files {
        let destination = safe_destination(data_root, relative)?;
        atomic_copy(source, &destination, 0o444)?;
    }

    atomic_write(&data_root.join("index.jsonl"), &index_bytes, 0o444)?;
    atomic_write(&data_root.join("manifest.json"), &manifest_bytes, 0o444)?;

    set_mode(data_root, 0o755)?;
    set_mode(&workspace, 0o770)?;
    Ok(())
}

fn validate_current_manifest(staged: &StagedTransfer, data_root: &Path) -> Result<()> {
    let path = data_root.join("manifest.json");
    let manifest: Value =
        read_json_file(&path, MAX_MANIFEST_JSON_BYTES, "current corpus manifest")?;
    let object = manifest.as_object().ok_or_else(|| {
        BootstrapError::InvalidArchive("current corpus manifest is not an object".to_owned())
    })?;
    let user_id = require_u64(object, "user_id", "current corpus manifest")?;
    if user_id != staged.transfer.user_id {
        return Err(BootstrapError::InvalidArchive(
            "current corpus belongs to a different user".to_owned(),
        ));
    }
    let revision = require_u64(object, "revision", "current corpus manifest")?;
    if revision != staged.transfer.from_revision {
        return Err(BootstrapError::InvalidArchive(format!(
            "corpus revision changed during delta install: {revision} != {}",
            staged.transfer.from_revision
        )));
    }
    Ok(())
}

fn read_index(path: &Path) -> Result<BTreeMap<String, Value>> {
    let metadata = regular_file_metadata(path, "inspecting corpus index")?;
    if metadata.len() > MAX_INDEX_BYTES {
        return Err(BootstrapError::InvalidArchive(format!(
            "corpus index exceeds {MAX_INDEX_BYTES} bytes"
        )));
    }
    let file = File::open(path)
        .map_err(|error| BootstrapError::io("opening corpus index", path, error))?;
    let mut reader = BufReader::new(file);
    let mut line = Vec::new();
    let mut records = BTreeMap::new();
    loop {
        line.clear();
        let read = reader
            .read_until(b'\n', &mut line)
            .map_err(|error| BootstrapError::io("reading corpus index", path, error))?;
        if read == 0 {
            break;
        }
        if line.len() > MAX_INDEX_LINE_BYTES {
            return Err(BootstrapError::InvalidArchive(format!(
                "corpus index line exceeds {MAX_INDEX_LINE_BYTES} bytes"
            )));
        }
        if line.iter().all(u8::is_ascii_whitespace) {
            continue;
        }
        let record: Value = serde_json::from_slice(&line)
            .map_err(|error| BootstrapError::json("decoding corpus index record", error))?;
        let path_value = record
            .get("path")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                BootstrapError::InvalidArchive(
                    "corpus index record must contain a string path".to_owned(),
                )
            })?
            .to_owned();
        validate_corpus_path(&path_value)?;
        if records.insert(path_value.clone(), record).is_some() {
            return Err(BootstrapError::InvalidArchive(format!(
                "corpus index contains duplicate path {path_value}"
            )));
        }
        if records.len() > MAX_INDEX_RECORDS {
            return Err(BootstrapError::InvalidArchive(format!(
                "corpus index contains more than {MAX_INDEX_RECORDS} records"
            )));
        }
    }
    Ok(records)
}

fn encode_index(records: &BTreeMap<String, Value>) -> Result<Vec<u8>> {
    let mut output = Vec::new();
    for record in records.values() {
        serde_json::to_writer(&mut output, record)
            .map_err(|error| BootstrapError::json("encoding corpus index record", error))?;
        output.push(b'\n');
        if output.len() as u64 > MAX_INDEX_BYTES {
            return Err(BootstrapError::InvalidArchive(format!(
                "resulting corpus index exceeds {MAX_INDEX_BYTES} bytes"
            )));
        }
    }
    Ok(output)
}

fn read_json_file<T: for<'de> Deserialize<'de>>(
    path: &Path,
    limit: u64,
    label: &'static str,
) -> Result<T> {
    let metadata = regular_file_metadata(path, "inspecting JSON file")?;
    if metadata.len() > limit {
        return Err(BootstrapError::InvalidArchive(format!(
            "{label} exceeds {limit} bytes"
        )));
    }
    let file =
        File::open(path).map_err(|error| BootstrapError::io("opening JSON file", path, error))?;
    serde_json::from_reader(BufReader::new(file))
        .map_err(|error| BootstrapError::json("decoding corpus archive JSON", error))
}

fn require_u64(object: &Map<String, Value>, key: &str, label: &str) -> Result<u64> {
    object.get(key).and_then(Value::as_u64).ok_or_else(|| {
        BootstrapError::InvalidArchive(format!("{label} field {key} must be a nonnegative integer"))
    })
}

fn regular_file_metadata(path: &Path, operation: &'static str) -> Result<fs::Metadata> {
    let metadata =
        fs::symlink_metadata(path).map_err(|error| BootstrapError::io(operation, path, error))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(BootstrapError::InvalidArchive(format!(
            "{} must be a regular file",
            path.display()
        )));
    }
    Ok(metadata)
}

fn ensure_directory_without_symlink(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => Err(
            BootstrapError::InvalidArchive(format!("{} must be a real directory", path.display())),
        ),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => fs::create_dir_all(path)
            .map_err(|error| BootstrapError::io("creating corpus directory", path, error)),
        Err(error) => Err(BootstrapError::io(
            "inspecting corpus directory",
            path,
            error,
        )),
    }
}

fn safe_destination(data_root: &Path, relative: &str) -> Result<PathBuf> {
    validate_corpus_path(relative)?;
    let destination = data_root.join(relative);
    ensure_safe_parent(
        data_root,
        destination.parent().ok_or_else(|| {
            BootstrapError::InvalidArchive("corpus destination has no parent".to_owned())
        })?,
    )?;
    Ok(destination)
}

fn ensure_safe_parent(data_root: &Path, parent: &Path) -> Result<()> {
    let relative = parent.strip_prefix(data_root).map_err(|_| {
        BootstrapError::InvalidArchive("corpus destination escaped /data".to_owned())
    })?;
    let mut current = data_root.to_path_buf();
    for component in relative.components() {
        current.push(component);
        match fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
                return Err(BootstrapError::InvalidArchive(format!(
                    "corpus parent {} is not a real directory",
                    current.display()
                )));
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                fs::create_dir(&current).map_err(|error| {
                    BootstrapError::io("creating corpus parent directory", &current, error)
                })?;
                set_mode(&current, 0o755)?;
            }
            Err(error) => {
                return Err(BootstrapError::io(
                    "inspecting corpus parent directory",
                    &current,
                    error,
                ));
            }
        }
    }
    Ok(())
}

fn remove_path_safely(data_root: &Path, relative: &str) -> Result<()> {
    validate_corpus_path_or_control_file(relative)?;
    let target = data_root.join(relative);
    let parent = target.parent().ok_or_else(|| {
        BootstrapError::InvalidArchive("corpus removal target has no parent".to_owned())
    })?;
    ensure_safe_parent(data_root, parent)?;
    let metadata = match fs::symlink_metadata(&target) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => {
            return Err(BootstrapError::io(
                "inspecting corpus removal target",
                &target,
                error,
            ));
        }
    };
    if metadata.is_dir() && !metadata.file_type().is_symlink() {
        fs::remove_dir_all(&target)
            .map_err(|error| BootstrapError::io("removing corpus directory", &target, error))
    } else {
        fs::remove_file(&target)
            .map_err(|error| BootstrapError::io("removing corpus file", &target, error))
    }
}

fn validate_corpus_path_or_control_file(value: &str) -> Result<()> {
    if matches!(value, "index.jsonl" | "manifest.json") || CORPUS_ROOTS.contains(&value) {
        validate_member_path(value)
    } else {
        validate_corpus_path(value)
    }
}

fn atomic_copy(source: &Path, destination: &Path, mode: u32) -> Result<()> {
    let mut source_file = File::open(source)
        .map_err(|error| BootstrapError::io("opening staged corpus file", source, error))?;
    let parent = destination.parent().ok_or_else(|| {
        BootstrapError::InvalidArchive("corpus destination has no parent".to_owned())
    })?;
    let mut temporary = NamedTempFile::new_in(parent)
        .map_err(|error| BootstrapError::io("creating atomic corpus file", destination, error))?;
    std::io::copy(&mut source_file, temporary.as_file_mut())
        .map_err(|error| BootstrapError::io("copying atomic corpus file", destination, error))?;
    persist_atomic(temporary, destination, mode)
}

fn atomic_write(destination: &Path, bytes: &[u8], mode: u32) -> Result<()> {
    let parent = destination.parent().ok_or_else(|| {
        BootstrapError::InvalidArchive("corpus destination has no parent".to_owned())
    })?;
    ensure_directory_without_symlink(parent)?;
    let mut temporary = NamedTempFile::new_in(parent)
        .map_err(|error| BootstrapError::io("creating atomic corpus file", destination, error))?;
    {
        let mut writer = BufWriter::new(temporary.as_file_mut());
        writer.write_all(bytes).map_err(|error| {
            BootstrapError::io("writing atomic corpus file", destination, error)
        })?;
        writer.flush().map_err(|error| {
            BootstrapError::io("flushing atomic corpus file", destination, error)
        })?;
    }
    persist_atomic(temporary, destination, mode)
}

fn persist_atomic(temporary: NamedTempFile, destination: &Path, mode: u32) -> Result<()> {
    temporary
        .as_file()
        .sync_all()
        .map_err(|error| BootstrapError::io("syncing atomic corpus file", destination, error))?;
    set_file_mode(temporary.as_file(), destination, mode)?;
    temporary.persist(destination).map_err(|error| {
        BootstrapError::io("publishing atomic corpus file", destination, error.error)
    })?;
    sync_directory(destination.parent().expect("destination has parent"))
}

fn sync_directory(path: &Path) -> Result<()> {
    File::open(path)
        .and_then(|directory| directory.sync_all())
        .map_err(|error| BootstrapError::io("syncing corpus directory", path, error))
}

#[cfg(unix)]
fn set_mode(path: &Path, mode: u32) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(mode))
        .map_err(|error| BootstrapError::io("setting corpus permissions", path, error))
}

#[cfg(not(unix))]
fn set_mode(_path: &Path, _mode: u32) -> Result<()> {
    Ok(())
}

#[cfg(unix)]
fn set_file_mode(file: &File, path: &Path, mode: u32) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    file.set_permissions(fs::Permissions::from_mode(mode))
        .map_err(|error| BootstrapError::io("setting corpus file permissions", path, error))
}

#[cfg(not(unix))]
fn set_file_mode(_file: &File, _path: &Path, _mode: u32) -> Result<()> {
    Ok(())
}

#[cfg(unix)]
fn configure_vm_workspace(data_root: &Path) -> Result<()> {
    use rustix::fs::{Gid, Uid, chown};

    let workspace = data_root.join("workspace");
    chown(
        &workspace,
        Some(Uid::from_raw(WORKSPACE_UID)),
        Some(Gid::from_raw(WORKSPACE_GID)),
    )
    .map_err(|error| {
        BootstrapError::io(
            "setting workspace ownership",
            &workspace,
            std::io::Error::from_raw_os_error(error.raw_os_error()),
        )
    })?;
    set_mode(data_root, 0o755)?;
    set_mode(&workspace, 0o770)
}

#[cfg(not(unix))]
fn configure_vm_workspace(data_root: &Path) -> Result<()> {
    let workspace = data_root.join("workspace");
    set_mode(data_root, 0o755)?;
    set_mode(&workspace, 0o770)
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::io::Cursor;

    use flate2::Compression;
    use flate2::write::GzEncoder;
    use tar::{Builder, EntryType, Header};
    use tempfile::tempdir;

    use super::{install_archive_at, validate_corpus_path, validate_member_path};

    #[test]
    fn full_then_delta_install_keeps_manifest_last_contract() {
        let fixture = tempdir().expect("fixture tempdir");
        let data_root = fixture.path().join("data");
        let full = fixture.path().join("full.tar.gz");
        build_archive(&full, true, 0, 1, 7, &[], &[("content/one.md", b"one")]);

        install_archive_at(&full, &data_root).expect("install full archive");
        assert_eq!(
            fs::read_to_string(data_root.join("content/one.md")).expect("read full document"),
            "one"
        );
        assert_eq!(manifest_revision(&data_root), 1);

        let delta = fixture.path().join("delta.tar.gz");
        build_archive(
            &delta,
            false,
            1,
            2,
            7,
            &["content/one.md"],
            &[("content/two.md", b"two")],
        );
        install_archive_at(&delta, &data_root).expect("install delta archive");

        assert!(!data_root.join("content/one.md").exists());
        assert_eq!(
            fs::read_to_string(data_root.join("content/two.md")).expect("read delta document"),
            "two"
        );
        assert_eq!(manifest_revision(&data_root), 2);
    }

    #[test]
    fn invalid_target_manifest_cannot_mutate_an_existing_corpus() {
        let fixture = tempdir().expect("fixture tempdir");
        let data_root = fixture.path().join("data");
        let full = fixture.path().join("full.tar.gz");
        build_archive(&full, true, 0, 1, 7, &[], &[("content/one.md", b"one")]);
        install_archive_at(&full, &data_root).expect("install full archive");

        let invalid = fixture.path().join("invalid.tar.gz");
        build_archive_with_manifest_revision(
            &invalid,
            false,
            1,
            2,
            99,
            7,
            &[],
            &[("content/two.md", b"two")],
        );
        assert!(install_archive_at(&invalid, &data_root).is_err());
        assert_eq!(manifest_revision(&data_root), 1);
        assert!(data_root.join("content/one.md").is_file());
        assert!(!data_root.join("content/two.md").exists());
    }

    #[test]
    fn archive_rejects_links_before_applying_any_files() {
        let fixture = tempdir().expect("fixture tempdir");
        let archive_path = fixture.path().join("link.tar.gz");
        let file = fs::File::create(&archive_path).expect("create link fixture");
        let encoder = GzEncoder::new(file, Compression::default());
        let mut builder = Builder::new(encoder);
        let mut header = Header::new_gnu();
        header.set_entry_type(EntryType::Symlink);
        header.set_size(0);
        header
            .set_link_name("/etc/passwd")
            .expect("set fixture link target");
        header.set_cksum();
        builder
            .append_data(&mut header, "files/content/escape.md", Cursor::new([]))
            .expect("append link fixture");
        builder
            .into_inner()
            .expect("finish tar")
            .finish()
            .expect("finish gzip");

        let error = install_archive_at(&archive_path, &fixture.path().join("data"))
            .expect_err("archive link must fail");
        assert!(
            error
                .to_string()
                .contains("links and special files are forbidden")
        );
    }

    #[test]
    fn path_validation_rejects_traversal_workspace_and_backslashes() {
        assert!(validate_member_path("../manifest.json").is_err());
        assert!(validate_member_path("files\\content\\one.md").is_err());
        assert!(validate_corpus_path("workspace/task/output").is_err());
        assert!(validate_corpus_path("content/one.md").is_ok());
    }

    fn build_archive(
        path: &std::path::Path,
        full: bool,
        from_revision: u64,
        to_revision: u64,
        user_id: u64,
        deletions: &[&str],
        files: &[(&str, &[u8])],
    ) {
        build_archive_with_manifest_revision(
            path,
            full,
            from_revision,
            to_revision,
            to_revision,
            user_id,
            deletions,
            files,
        );
    }

    #[allow(clippy::too_many_arguments)]
    fn build_archive_with_manifest_revision(
        path: &std::path::Path,
        full: bool,
        from_revision: u64,
        to_revision: u64,
        manifest_revision: u64,
        user_id: u64,
        deletions: &[&str],
        files: &[(&str, &[u8])],
    ) {
        let file = fs::File::create(path).expect("create archive fixture");
        let encoder = GzEncoder::new(file, Compression::default());
        let mut builder = Builder::new(encoder);
        append_json(
            &mut builder,
            "transfer.json",
            &serde_json::json!({
                "version": 1,
                "user_id": user_id,
                "from_revision": from_revision,
                "to_revision": to_revision,
                "full": full,
            }),
        );
        append_json(
            &mut builder,
            "manifest.json",
            &serde_json::json!({
                "version": 2,
                "user_id": user_id,
                "revision": manifest_revision,
                "generated_at": "2026-08-30T00:00:00Z",
                "file_count": files.len(),
                "complete": true,
            }),
        );
        append_json(&mut builder, "deletions.json", &deletions);
        let mut index = Vec::new();
        for (name, contents) in files {
            serde_json::to_writer(&mut index, &serde_json::json!({"path": name}))
                .expect("encode index fixture");
            index.push(b'\n');
            append_bytes(&mut builder, &format!("files/{name}"), contents);
        }
        append_bytes(
            &mut builder,
            if full {
                "index_full.jsonl"
            } else {
                "index_upserts.jsonl"
            },
            &index,
        );
        builder
            .into_inner()
            .expect("finish tar")
            .finish()
            .expect("finish gzip");
    }

    fn append_json<T: serde::Serialize>(
        builder: &mut Builder<GzEncoder<fs::File>>,
        name: &str,
        value: &T,
    ) {
        let mut bytes = serde_json::to_vec(value).expect("encode JSON fixture");
        bytes.push(b'\n');
        append_bytes(builder, name, &bytes);
    }

    fn append_bytes(builder: &mut Builder<GzEncoder<fs::File>>, name: &str, bytes: &[u8]) {
        let mut header = Header::new_gnu();
        header.set_size(bytes.len() as u64);
        header.set_mode(0o444);
        header.set_cksum();
        builder
            .append_data(&mut header, name, Cursor::new(bytes))
            .expect("append archive fixture");
    }

    fn manifest_revision(data_root: &std::path::Path) -> u64 {
        serde_json::from_slice::<serde_json::Value>(
            &fs::read(data_root.join("manifest.json")).expect("read manifest fixture"),
        )
        .expect("decode manifest fixture")["revision"]
            .as_u64()
            .expect("manifest revision")
    }
}
