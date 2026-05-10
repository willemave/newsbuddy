#!/usr/bin/env python3
"""Script to requeue podcast media processing for all podcasts."""

import sys
from pathlib import Path

# Add the app directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.db import Content
from app.services.queue import TaskType, get_queue_service

logger = get_logger(__name__)


def main():
    """Main function to requeue podcast media tasks."""
    # Create database session
    settings = get_settings()
    engine = create_engine(str(settings.database_url))
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Find all podcast content
        podcasts = session.query(Content).filter(Content.content_type == "podcast").all()

        logger.info(f"Found {len(podcasts)} podcasts to requeue for media processing")

        queue_service = get_queue_service()
        tasks_created = 0
        for podcast in podcasts:
            queue_service.enqueue(
                TaskType.PROCESS_PODCAST_MEDIA,
                content_id=podcast.id,
            )
            tasks_created += 1

            logger.info(f"Queued media processing task for podcast {podcast.id}: {podcast.title}")

        logger.info(f"Successfully queued {tasks_created} media tasks")

    except Exception as e:
        logger.error(f"Error creating media tasks: {e}")
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
