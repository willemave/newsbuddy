from __future__ import annotations

from contextlib import contextmanager

from app.models.contracts import ContentType
from app.models.db import Content, ContentBody
from app.pipeline.podcast_workers import PodcastMediaWorker
from app.services.apple_podcasts import ApplePodcastResolution
from app.services.queue import TaskType


def test_youtube_audio_media_task_queues_summarize(db_session, mocker, tmp_path):
    youtube_url = "https://www.youtube.com/watch?v=abc123xyz"
    content = Content(
        content_type=ContentType.PODCAST.value,
        url=youtube_url,
        content_metadata={"audio_url": youtube_url},
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

    audio_path = worker.scratch_root / f"content-{content.id}" / "youtube" / "test-audio.webm"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"audio-bytes")

    mocker.patch.object(worker, "_download_youtube_audio", return_value=audio_path)
    mocker.patch.object(worker, "_normalize_audio_file", return_value=audio_path)
    mocker.patch(
        "app.pipeline.podcast_workers.transcribe_audio_file_with_metadata",
        return_value=("YouTube transcript", "en"),
    )

    assert worker.process_media_task(content.id) is True

    db_session.refresh(content)
    metadata = content.content_metadata

    assert metadata["has_transcript"] is True
    assert metadata["detected_language"] == "en"
    assert "download_skipped" not in metadata
    body = db_session.query(ContentBody).filter(ContentBody.content_id == content.id).one()
    assert body.char_count == len("YouTube transcript")

    worker.queue_service.enqueue.assert_called_once_with(TaskType.SUMMARIZE, content_id=content.id)


def test_apple_podcasts_resolution_fills_audio_url(db_session, mocker, tmp_path):
    apple_url = (
        "https://podcasts.apple.com/us/podcast/chatgpt-5-5-coming-soon/id1680633614?i=1000745224972"
    )
    content = Content(
        content_type=ContentType.PODCAST.value,
        url=apple_url,
        title=None,
        platform="apple_podcasts",
        content_metadata={"platform": "apple_podcasts", "submitted_by_user_id": 17},
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
    pipeline_http_service = mocker.Mock()

    def _resolve(url: str, *, feed_fetch) -> ApplePodcastResolution:  # noqa: ANN001
        assert url == apple_url
        assert feed_fetch == pipeline_http_service.fetch_bounded_public
        return ApplePodcastResolution(
            feed_url="https://example.com/feed.xml",
            episode_title="Episode Title",
            audio_url="https://example.com/audio.mp3",
        )

    mocker.patch(
        "app.pipeline.podcast_workers.get_http_service",
        return_value=pipeline_http_service,
    )
    mocker.patch(
        "app.pipeline.podcast_workers.resolve_apple_podcast_episode",
        side_effect=_resolve,
    )

    audio_path = tmp_path / "episode.mp3"
    audio_path.write_bytes(b"audio-bytes")
    mocker.patch.object(worker, "_download_to_scratch", return_value=audio_path)
    mocker.patch.object(worker, "_normalize_audio_file", return_value=audio_path)
    mocker.patch(
        "app.pipeline.podcast_workers.transcribe_audio_file_with_metadata",
        return_value=("Transcript text", "en"),
    )

    assert worker.process_media_task(content.id) is True

    db_session.refresh(content)
    metadata = content.content_metadata
    assert metadata.get("audio_url") == "https://example.com/audio.mp3"
    assert metadata.get("feed_url") == "https://example.com/feed.xml"
    assert metadata.get("episode_title") == "Episode Title"
    worker.queue_service.enqueue.assert_called_once_with(TaskType.SUMMARIZE, content_id=content.id)


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
    assert download.call_args.kwargs["sandbox_user_id"] == 0
    worker.queue_service.enqueue.assert_called_once_with(TaskType.SUMMARIZE, content_id=content.id)


def test_non_youtube_media_download_uses_sandbox_for_link_local_url(mocker, tmp_path):
    worker = PodcastMediaWorker()
    audio_bytes = b"sandbox-audio"
    host_http = mocker.patch(
        "httpx.Client",
        side_effect=AssertionError("untrusted media must not use host HTTP"),
    )

    def _sandbox_download(url, destination, *, user_id, execution_id):
        assert url == "http://169.254.169.254/private.mp3"
        assert user_id == 17
        assert execution_id == 42
        destination.write_bytes(audio_bytes)
        return destination

    sandbox_download = mocker.patch(
        "app.pipeline.podcast_workers.download_remote_media_in_sandbox",
        side_effect=_sandbox_download,
    )

    result = worker._download_to_scratch(
        content_id=42,
        title="Private episode",
        audio_url="http://169.254.169.254/private.mp3",
        scratch_dir=tmp_path,
        sandbox_user_id=17,
    )

    assert result.read_bytes() == audio_bytes
    sandbox_download.assert_called_once()
    host_http.assert_not_called()


def test_youtube_media_download_keeps_existing_ytdlp_path(mocker, tmp_path):
    worker = PodcastMediaWorker()
    expected = tmp_path / "youtube" / "episode.webm"
    expected.parent.mkdir()
    expected.write_bytes(b"youtube-audio")
    youtube_download = mocker.patch.object(
        worker,
        "_download_youtube_audio",
        return_value=expected,
    )
    sandbox_download = mocker.patch("app.pipeline.podcast_workers.download_remote_media_in_sandbox")

    result = worker._download_to_scratch(
        content_id=42,
        title="YouTube episode",
        audio_url="https://www.youtube.com/watch?v=abc123xyz",
        scratch_dir=tmp_path,
        sandbox_user_id=17,
    )

    assert result == expected
    youtube_download.assert_called_once()
    sandbox_download.assert_not_called()
