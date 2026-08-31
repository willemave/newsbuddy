use std::io::Cursor;
use std::path::{Component, Path, PathBuf};

use image::imageops::FilterType;
use image::{GenericImageView, ImageFormat, ImageReader};
use thiserror::Error;
use tokio::fs;
use uuid::Uuid;

const THUMBNAIL_BOUND: u32 = 200;
const MAX_IMAGE_DIMENSION: u32 = 8_192;
const MAX_IMAGE_PIXELS: u64 = 40_000_000;

#[derive(Debug, Clone)]
pub struct ImageFileStore {
    root: PathBuf,
    max_image_bytes: usize,
}

impl ImageFileStore {
    /// Resolves a filesystem root and validates the encoded-image byte bound.
    ///
    /// # Errors
    ///
    /// Returns an error when the byte bound is zero or a relative root cannot be resolved against
    /// the current process directory.
    pub fn new(root: PathBuf, max_image_bytes: usize) -> Result<Self, ImageFileStoreError> {
        if max_image_bytes == 0 {
            return Err(ImageFileStoreError::InvalidConfiguration(
                "max_image_bytes must be greater than zero".to_owned(),
            ));
        }
        if root.as_os_str().is_empty()
            || root == Path::new("/")
            || root
                .components()
                .any(|component| matches!(component, Component::ParentDir))
        {
            return Err(ImageFileStoreError::InvalidConfiguration(
                "image root must be a scoped path without parent traversal".to_owned(),
            ));
        }
        let root = if root.is_absolute() {
            root
        } else {
            std::env::current_dir()
                .map_err(ImageFileStoreError::CurrentDirectory)?
                .join(root)
        };
        Ok(Self {
            root,
            max_image_bytes,
        })
    }

    pub(super) async fn stage(
        &self,
        content_id: i64,
        task_id: i64,
        bytes: &[u8],
    ) -> Result<StagedImage, ImageFileStoreError> {
        if bytes.is_empty() {
            return Err(ImageFileStoreError::EmptyImage);
        }
        if bytes.len() > self.max_image_bytes {
            return Err(ImageFileStoreError::ImageTooLarge {
                actual: bytes.len(),
                limit: self.max_image_bytes,
            });
        }
        let source = bytes.to_vec();
        let max_image_bytes = self.max_image_bytes;
        let transformed =
            tokio::task::spawn_blocking(move || transform_image(&source, max_image_bytes))
                .await
                .map_err(ImageFileStoreError::TransformTask)??;

        let content_dir = self.root.join("content");
        let thumbnail_dir = self.root.join("thumbnails");
        let staging_dir = self.root.join(".staging");
        fs::create_dir_all(&content_dir).await?;
        fs::create_dir_all(&thumbnail_dir).await?;
        fs::create_dir_all(&staging_dir).await?;
        let nonce = Uuid::new_v4().simple();
        let staged_image_path =
            staging_dir.join(format!("content-{content_id}-task-{task_id}-{nonce}.png"));
        let staged_thumbnail_path =
            staging_dir.join(format!("thumbnail-{content_id}-task-{task_id}-{nonce}.png"));
        fs::write(&staged_image_path, transformed.normalized).await?;
        if let Err(error) = fs::write(&staged_thumbnail_path, transformed.thumbnail).await {
            let _ = fs::remove_file(&staged_image_path).await;
            return Err(ImageFileStoreError::Io(error));
        }
        Ok(StagedImage {
            image_temp: staged_image_path,
            thumbnail_temp: staged_thumbnail_path,
            image_destination: content_dir.join(format!("{content_id}.png")),
            thumbnail_destination: thumbnail_dir.join(format!("{content_id}.png")),
        })
    }
}

#[derive(Debug)]
struct TransformedImage {
    normalized: Vec<u8>,
    thumbnail: Vec<u8>,
}

fn transform_image(
    bytes: &[u8],
    max_image_bytes: usize,
) -> Result<TransformedImage, ImageFileStoreError> {
    let dimensions = ImageReader::new(Cursor::new(bytes))
        .with_guessed_format()?
        .into_dimensions()?;
    validate_dimensions(dimensions.0, dimensions.1)?;
    let source_format = image::guess_format(bytes)?;
    let image = image::load_from_memory_with_format(bytes, source_format)?;
    let (width, height) = image.dimensions();
    validate_dimensions(width, height)?;

    let mut normalized = Cursor::new(Vec::new());
    image.write_to(&mut normalized, ImageFormat::Png)?;
    let normalized = normalized.into_inner();
    if normalized.len() > max_image_bytes {
        return Err(ImageFileStoreError::ImageTooLarge {
            actual: normalized.len(),
            limit: max_image_bytes,
        });
    }

    let (thumbnail_width, thumbnail_height) = thumbnail_dimensions(width, height);
    let thumbnail = image.resize(thumbnail_width, thumbnail_height, FilterType::Lanczos3);
    let mut thumbnail_bytes = Cursor::new(Vec::new());
    thumbnail.write_to(&mut thumbnail_bytes, ImageFormat::Png)?;
    Ok(TransformedImage {
        normalized,
        thumbnail: thumbnail_bytes.into_inner(),
    })
}

#[derive(Debug)]
pub(super) struct StagedImage {
    image_temp: PathBuf,
    thumbnail_temp: PathBuf,
    image_destination: PathBuf,
    thumbnail_destination: PathBuf,
}

impl StagedImage {
    /// Publishes only after the worker kernel has locked the exact live queue lease. The two local
    /// renames are bounded filesystem operations; no provider or unbounded file work occurs while
    /// `PostgreSQL` is held.
    pub(super) async fn publish(&self) -> Result<(), ImageFileStoreError> {
        publish_one(&self.image_temp, &self.image_destination).await?;
        publish_one(&self.thumbnail_temp, &self.thumbnail_destination).await?;
        Ok(())
    }

    pub(super) async fn cleanup(&self) {
        let _ = fs::remove_file(&self.image_temp).await;
        let _ = fs::remove_file(&self.thumbnail_temp).await;
    }
}

impl Drop for StagedImage {
    fn drop(&mut self) {
        // A rejected finalization never reaches `after_commit`. Best-effort synchronous cleanup
        // prevents lost-lease attempts from accumulating unpublished image payloads.
        let _ = std::fs::remove_file(&self.image_temp);
        let _ = std::fs::remove_file(&self.thumbnail_temp);
    }
}

async fn publish_one(staged: &Path, canonical: &Path) -> Result<(), ImageFileStoreError> {
    fs::rename(staged, canonical).await?;
    Ok(())
}

fn validate_dimensions(width: u32, height: u32) -> Result<(), ImageFileStoreError> {
    if width == 0 || height == 0 {
        return Err(ImageFileStoreError::InvalidDimensions { width, height });
    }
    let pixels = u64::from(width).saturating_mul(u64::from(height));
    if width > MAX_IMAGE_DIMENSION || height > MAX_IMAGE_DIMENSION || pixels > MAX_IMAGE_PIXELS {
        return Err(ImageFileStoreError::InvalidDimensions { width, height });
    }
    Ok(())
}

fn thumbnail_dimensions(width: u32, height: u32) -> (u32, u32) {
    let largest = width.max(height);
    if largest <= THUMBNAIL_BOUND {
        return (width, height);
    }
    let largest = u64::from(largest);
    let scaled = |dimension: u32| {
        let rounded = u64::from(dimension)
            .saturating_mul(u64::from(THUMBNAIL_BOUND))
            .saturating_add(largest / 2)
            / largest;
        u32::try_from(rounded.max(1)).unwrap_or(THUMBNAIL_BOUND)
    };
    (scaled(width), scaled(height))
}

#[derive(Debug, Error)]
pub enum ImageFileStoreError {
    #[error("invalid image storage configuration: {0}")]
    InvalidConfiguration(String),
    #[error("could not resolve the current directory for image storage")]
    CurrentDirectory(#[source] std::io::Error),
    #[error("image provider returned an empty image")]
    EmptyImage,
    #[error("image has {actual} bytes, exceeding the {limit}-byte bound")]
    ImageTooLarge { actual: usize, limit: usize },
    #[error("image dimensions {width}x{height} exceed the worker bound")]
    InvalidDimensions { width: u32, height: u32 },
    #[error("image data could not be decoded or encoded")]
    Image(#[from] image::ImageError),
    #[error("image transform task failed")]
    TransformTask(#[source] tokio::task::JoinError),
    #[error("image storage operation failed")]
    Io(#[from] std::io::Error),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_oversized_dimensions_before_decode() {
        assert!(validate_dimensions(10_000, 100).is_err());
        assert!(validate_dimensions(8_192, 8_192).is_err());
        assert!(validate_dimensions(1_600, 900).is_ok());
    }

    #[tokio::test]
    async fn stages_normalized_image_and_thumbnail_before_canonical_publish() {
        let temporary = tempfile::tempdir().expect("temporary image root should exist");
        let source = image::DynamicImage::ImageRgb8(image::RgbImage::from_pixel(
            640,
            360,
            image::Rgb([24, 80, 160]),
        ));
        let mut bytes = Cursor::new(Vec::new());
        source
            .write_to(&mut bytes, ImageFormat::Png)
            .expect("source image should encode");
        let store = ImageFileStore::new(temporary.path().to_path_buf(), 2_000_000)
            .expect("store configuration should be valid");

        let staged = store
            .stage(42, 99, bytes.get_ref())
            .await
            .expect("valid image should stage");
        assert!(staged.image_temp.is_file());
        assert!(staged.thumbnail_temp.is_file());
        assert!(!staged.image_destination.exists());

        staged.publish().await.expect("staged image should publish");
        assert!(temporary.path().join("content/42.png").is_file());
        let thumbnail = image::open(temporary.path().join("thumbnails/42.png"))
            .expect("thumbnail should decode");
        assert_eq!(thumbnail.dimensions(), (200, 113));
    }
}
