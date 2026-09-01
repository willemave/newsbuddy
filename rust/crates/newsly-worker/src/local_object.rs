use std::path::{Component, Path, PathBuf};

use sha2::{Digest, Sha256};
use tokio::fs;
use tokio::io::AsyncWriteExt;
use uuid::Uuid;

#[derive(Debug)]
pub(crate) enum LocalObjectError {
    CurrentDirectory(std::io::Error),
    UnsafePath,
    Write(std::io::Error),
    Read(std::io::Error),
    UnsafeExistingObject,
}

pub(crate) fn absolute_root(root: PathBuf) -> Result<PathBuf, LocalObjectError> {
    if root.is_absolute() {
        Ok(root)
    } else {
        std::env::current_dir()
            .map(|current_directory| current_directory.join(root))
            .map_err(LocalObjectError::CurrentDirectory)
    }
}

pub(crate) fn validate_relative_path(path: &Path) -> Result<(), LocalObjectError> {
    if path.as_os_str().is_empty()
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(LocalObjectError::UnsafePath);
    }
    Ok(())
}

pub(crate) fn storage_key(path: &Path) -> Result<String, LocalObjectError> {
    validate_relative_path(path)?;
    path.components()
        .map(|component| match component {
            Component::Normal(part) => part
                .to_str()
                .map(str::to_owned)
                .ok_or(LocalObjectError::UnsafePath),
            _ => Err(LocalObjectError::UnsafePath),
        })
        .collect::<Result<Vec<_>, _>>()
        .map(|parts| parts.join("/"))
}

pub(crate) fn sha256_hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut encoded = String::with_capacity(digest.len() * 2);
    for byte in digest {
        use std::fmt::Write as _;
        write!(encoded, "{byte:02x}").expect("writing to String cannot fail");
    }
    encoded
}

/// Publishes immutable bytes without replacing an existing destination.
///
/// The complete temporary file is created beside the destination, then hard-linked into place.
/// A concurrent winner therefore cannot be clobbered; an existing object is accepted only when it
/// is a regular non-symlink file with the exact expected bytes.
pub(crate) async fn publish(
    root: &Path,
    relative: &Path,
    bytes: &[u8],
) -> Result<(), LocalObjectError> {
    validate_relative_path(relative)?;
    let destination = root.join(relative);
    let parent = destination.parent().ok_or(LocalObjectError::UnsafePath)?;
    fs::create_dir_all(parent)
        .await
        .map_err(LocalObjectError::Write)?;

    let temporary = parent.join(format!(".{}.tmp", Uuid::new_v4()));
    if let Err(error) = write_new_file(&temporary, bytes).await {
        let _ = fs::remove_file(&temporary).await;
        return Err(error);
    }
    match fs::hard_link(&temporary, &destination).await {
        Ok(()) => remove_temporary(&temporary).await,
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            remove_temporary(&temporary).await?;
            validate_existing(&destination, bytes).await
        }
        Err(error) => {
            let _ = fs::remove_file(&temporary).await;
            Err(LocalObjectError::Write(error))
        }
    }
}

pub(crate) async fn read_optional(
    root: &Path,
    relative: &Path,
) -> Result<Option<Vec<u8>>, LocalObjectError> {
    validate_relative_path(relative)?;
    let path = root.join(relative);
    let metadata = match fs::symlink_metadata(&path).await {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(LocalObjectError::Read(error)),
    };
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(LocalObjectError::UnsafeExistingObject);
    }
    fs::read(path)
        .await
        .map(Some)
        .map_err(LocalObjectError::Read)
}

async fn write_new_file(path: &Path, bytes: &[u8]) -> Result<(), LocalObjectError> {
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .await
        .map_err(LocalObjectError::Write)?;
    file.write_all(bytes)
        .await
        .map_err(LocalObjectError::Write)?;
    file.flush().await.map_err(LocalObjectError::Write)
}

async fn remove_temporary(path: &Path) -> Result<(), LocalObjectError> {
    fs::remove_file(path).await.map_err(LocalObjectError::Write)
}

async fn validate_existing(path: &Path, expected: &[u8]) -> Result<(), LocalObjectError> {
    let metadata = fs::symlink_metadata(path)
        .await
        .map_err(LocalObjectError::Read)?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() != expected.len() as u64
    {
        return Err(LocalObjectError::UnsafeExistingObject);
    }
    let actual = fs::read(path).await.map_err(LocalObjectError::Read)?;
    if actual == expected {
        Ok(())
    } else {
        Err(LocalObjectError::UnsafeExistingObject)
    }
}

#[cfg(test)]
mod tests {
    use std::path::{Path, PathBuf};

    use tokio::fs;
    use uuid::Uuid;

    use super::{LocalObjectError, publish};

    fn test_root(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!("newsly-local-object-{label}-{}", Uuid::new_v4()))
    }

    #[tokio::test]
    async fn exact_existing_bytes_are_idempotent() {
        let root = test_root("idempotent");
        let relative = Path::new("objects/value.txt");

        publish(&root, relative, b"expected").await.unwrap();
        publish(&root, relative, b"expected").await.unwrap();

        assert_eq!(fs::read(root.join(relative)).await.unwrap(), b"expected");
        fs::remove_dir_all(root).await.unwrap();
    }

    #[tokio::test]
    async fn different_existing_bytes_are_rejected_without_replacement() {
        let root = test_root("different");
        let relative = Path::new("objects/value.txt");
        publish(&root, relative, b"original").await.unwrap();

        let error = publish(&root, relative, b"different").await.unwrap_err();

        assert!(matches!(error, LocalObjectError::UnsafeExistingObject));
        assert_eq!(fs::read(root.join(relative)).await.unwrap(), b"original");
        fs::remove_dir_all(root).await.unwrap();
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn existing_symlink_is_rejected() {
        use std::os::unix::fs::symlink;

        let root = test_root("symlink");
        let relative = Path::new("objects/value.txt");
        let destination = root.join(relative);
        fs::create_dir_all(destination.parent().unwrap())
            .await
            .unwrap();
        let target = root.join("target.txt");
        fs::write(&target, b"expected").await.unwrap();
        symlink(&target, &destination).unwrap();

        let error = publish(&root, relative, b"expected").await.unwrap_err();

        assert!(matches!(error, LocalObjectError::UnsafeExistingObject));
        assert!(
            fs::symlink_metadata(destination)
                .await
                .unwrap()
                .file_type()
                .is_symlink()
        );
        fs::remove_dir_all(root).await.unwrap();
    }

    #[tokio::test]
    async fn concurrent_identical_writers_both_succeed() {
        let root = test_root("concurrent");
        let relative = Path::new("objects/value.txt");

        let (first, second) = tokio::join!(
            publish(&root, relative, b"expected"),
            publish(&root, relative, b"expected"),
        );

        first.unwrap();
        second.unwrap();
        assert_eq!(fs::read(root.join(relative)).await.unwrap(), b"expected");
        fs::remove_dir_all(root).await.unwrap();
    }
}
