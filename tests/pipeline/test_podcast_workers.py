from __future__ import annotations

from contextlib import contextmanager

from app.models.metadata import ContentType
from app.models.schema import Content, ContentBody
from app.pipeline.podcast_workers import PodcastDownloadWorker, PodcastMediaWorker
from app.services.apple_podcasts import ApplePodcastResolution
from app.services.queue import TaskType


def test_youtube_audio_download_queues_transcribe(db_session, mocker, tmp_path):
    youtube_url = "https://www.youtube.com/watch?v=abc123xyz"
    content = Content(
        content_type=ContentType.PODCAST.value,
        url=youtube_url,
        content_metadata={"audio_url": youtube_url},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    worker = PodcastDownloadWorker()
    worker.base_dir = tmp_path / "podcasts"
    worker.queue_service = mocker.Mock()

    @contextmanager
    def _get_db():
        yield db_session

    mocker.patch("app.pipeline.podcast_workers.get_db", _get_db)

    audio_path = worker.base_dir / "youtube" / "test-audio.webm"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"audio-bytes")

    mocker.patch.object(worker, "_download_youtube_audio", return_value=audio_path)

    assert worker.process_download_task(content.id) is True

    db_session.refresh(content)
    metadata = content.content_metadata

    assert metadata["youtube_video"] is True
    assert metadata["file_path"] == str(audio_path)
    assert metadata["file_size"] == audio_path.stat().st_size
    assert "download_skipped" not in metadata

    worker.queue_service.enqueue.assert_called_once_with(TaskType.TRANSCRIBE, content_id=content.id)


def test_apple_podcasts_resolution_fills_audio_url(db_session, mocker, tmp_path):
    apple_url = (
        "https://podcasts.apple.com/us/podcast/chatgpt-5-5-coming-soon/id1680633614?i=1000745224972"
    )
    content = Content(
        content_type=ContentType.PODCAST.value,
        url=apple_url,
        title=None,
        platform="apple_podcasts",
        content_metadata={"platform": "apple_podcasts"},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    worker = PodcastDownloadWorker()
    worker.base_dir = tmp_path / "podcasts"
    worker.queue_service = mocker.Mock()

    @contextmanager
    def _get_db():
        yield db_session

    mocker.patch("app.pipeline.podcast_workers.get_db", _get_db)
    mocker.patch(
        "app.pipeline.podcast_workers.resolve_apple_podcast_episode",
        return_value=ApplePodcastResolution(
            feed_url="https://example.com/feed.xml",
            episode_title="Episode Title",
            audio_url="https://example.com/audio.mp3",
        ),
    )

    def _fake_download(audio_url, file_path):  # noqa: ANN001
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"audio-bytes")

    mocker.patch.object(worker, "_download_with_retry", side_effect=_fake_download)

    assert worker.process_download_task(content.id) is True

    db_session.refresh(content)
    metadata = content.content_metadata
    assert metadata.get("audio_url") == "https://example.com/audio.mp3"
    assert metadata.get("feed_url") == "https://example.com/feed.xml"
    assert metadata.get("episode_title") == "Episode Title"

    worker.queue_service.enqueue.assert_called_once_with(TaskType.TRANSCRIBE, content_id=content.id)


def test_podcast_media_uses_direct_content_url_when_audio_metadata_missing(
    db_session,
    mocker,
    tmp_path,
):
    audio_url = "https://api.riverside.com/media/episode.mp3"
    content = Content(
        content_type=ContentType.PODCAST.value,
        url=audio_url,
        title="Reset Podcast Episode",
        status="processing",
        content_metadata={},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    worker = PodcastMediaWorker()
    worker.scratch_root = tmp_path / "scratch"
    worker.queue_service = mocker.Mock()

    @contextmanager
    def _get_db():
        yield db_session

    mocker.patch("app.pipeline.podcast_workers.get_db", _get_db)
    audio_path = tmp_path / "episode.mp3"
    audio_path.write_bytes(b"audio-bytes")
    download = mocker.patch.object(worker, "_download_to_scratch", return_value=audio_path)
    mocker.patch.object(worker, "_normalize_audio_file", return_value=audio_path)
    mocker.patch.object(worker.transcribe_worker, "_get_transcription_service")
    mocker.patch(
        "app.pipeline.podcast_workers.transcribe_audio_file_with_metadata",
        return_value=("Transcript text", "en"),
    )

    assert worker.process_media_task(content.id) is True

    db_session.refresh(content)
    assert content.content_metadata["audio_url"] == audio_url
    assert content.content_metadata["has_transcript"] is True
    assert content.content_metadata["detected_language"] == "en"
    body = db_session.query(ContentBody).filter(ContentBody.content_id == content.id).one()
    assert body.variant == "source"
    assert body.char_count == len("Transcript text")
    download.assert_called_once()
    assert download.call_args.kwargs["audio_url"] == audio_url
    worker.queue_service.enqueue.assert_called_once_with(TaskType.SUMMARIZE, content_id=content.id)
