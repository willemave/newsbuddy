#!/usr/bin/env python3
"""Generate 200px thumbnails from existing full-size images.

This script creates optimized thumbnails for fast loading in list views.
Thumbnails are created from:
- {IMAGES_BASE_DIR}/content/*.png (article/podcast infographics)
- {IMAGES_BASE_DIR}/news_thumbnails/*.png (news AI thumbnails)

Output:
- {IMAGES_BASE_DIR}/thumbnails/*.png (200x200 max thumbnails)

Usage:
    python scripts/resize_thumbnails.py --dry-run
    python scripts/resize_thumbnails.py
    python scripts/resize_thumbnails.py --force  # Regenerate existing
"""

import argparse
import os
import sys
from pathlib import Path

# Add parent directory so we can import from app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.utils.image_paths import (  # noqa: E402
    get_content_images_dir,
    get_news_thumbnails_dir,
    get_thumbnails_dir,
)

setup_logging()
logger = get_logger(__name__)

# Image directories
CONTENT_IMAGES_DIR = get_content_images_dir()
NEWS_THUMBNAILS_DIR = get_news_thumbnails_dir()
THUMBNAILS_DIR = get_thumbnails_dir()

# Thumbnail settings
THUMBNAIL_SIZE = (200, 200)


def generate_thumbnail(source_path: Path, dest_path: Path) -> bool:
    """Generate a thumbnail from a source image.

    Args:
        source_path: Path to the full-size image.
        dest_path: Path to save the thumbnail.

    Returns:
        True if thumbnail was created successfully.
    """
    try:
        with Image.open(source_path) as img:
            working_img: Image.Image = img
            # Convert to RGB if necessary (for PNG with transparency)
            if working_img.mode in ("RGBA", "P"):
                working_img = working_img.convert("RGB")

            # Use LANCZOS resampling for high-quality downscaling
            working_img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)

            # Save with optimization
            working_img.save(dest_path, "PNG", optimize=True)

        return True

    except Exception as e:
        logger.warning("Failed to generate thumbnail from %s: %s", source_path, e)
        return False


def generate_all_thumbnails(
    dry_run: bool = False,
    force: bool = False,
) -> None:
    """Generate thumbnails for all existing images.

    Args:
        dry_run: Show what would be done without making changes.
        force: Regenerate thumbnails even if they already exist.
    """
    print("Thumbnail Resize Script")
    print(f"  dry_run={dry_run}")
    print(f"  force={force}")
    print()

    # Ensure output directory exists
    if not dry_run:
        THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

    # Collect all source images
    source_images: list[tuple[Path, str]] = []

    # Content images (articles/podcasts)
    if CONTENT_IMAGES_DIR.exists():
        for img_path in CONTENT_IMAGES_DIR.glob("*.png"):
            source_images.append((img_path, "content"))

    # News thumbnails
    if NEWS_THUMBNAILS_DIR.exists():
        for img_path in NEWS_THUMBNAILS_DIR.glob("*.png"):
            source_images.append((img_path, "news"))

    print(f"Found {len(source_images)} source images")
    print()

    created = 0
    skipped_existing = 0
    failed = 0

    for source_path, source_type in source_images:
        # Extract content ID from filename
        content_id = source_path.stem

        dest_path = THUMBNAILS_DIR / f"{content_id}.png"

        # Skip if thumbnail already exists (unless force)
        if dest_path.exists() and not force:
            skipped_existing += 1
            continue

        if dry_run:
            print(f"  Would create: {dest_path.name} (from {source_type}/{source_path.name})")
            created += 1
        else:
            if generate_thumbnail(source_path, dest_path):
                logger.debug("Created thumbnail: %s", dest_path)
                created += 1
            else:
                failed += 1

    print("\nSummary:")
    print(f"  Total source images: {len(source_images)}")
    if dry_run:
        print(f"  Would create thumbnails: {created}")
    else:
        print(f"  Thumbnails created: {created}")
    print(f"  Skipped (already exists): {skipped_existing}")
    if failed > 0:
        print(f"  Failed: {failed}")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate 200px thumbnails from existing full-size images"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate thumbnails even if they already exist",
    )

    args = parser.parse_args()

    generate_all_thumbnails(
        dry_run=args.dry_run,
        force=args.force,
    )


if __name__ == "__main__":
    main()
