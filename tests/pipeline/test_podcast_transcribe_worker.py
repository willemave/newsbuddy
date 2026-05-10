from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content
from app.models.domain.content import ContentData
from app.pipeline.podcast_workers import PodcastTranscribeWorker
from app.services.queue import TaskType


@pytest.fixture
def worker():
    """Create a PodcastTranscribeWorker instance."""
    return PodcastTranscribeWorker()


@pytest.fixture
def mock_db_content():
    """Create a mock database content object."""
    content = Mock(spec=Content)
    content.id = 1
    content.title = "Test Podcast Episode"
    content.status = ContentStatus.PROCESSING.value
    content.content_metadata = {
        "file_path": "/path/to/test.mp3",
        "audio_url": "https://example.com/test.mp3",
    }
    content.retry_count = 0
    return content


@pytest.fixture
def mock_domain_content():
    """Create a mock domain content object."""
    return ContentData(
        id=1,
        content_type=ContentType.PODCAST,
        url="https://example.com/test.mp3",
        title="Test Podcast Episode",
        status=ContentStatus.PROCESSING,
        metadata={"file_path": "/path/to/test.mp3", "audio_url": "https://example.com/test.mp3"},
    )


class TestPodcastTranscribeWorker:
    """Test cases for PodcastTranscribeWorker."""

    @patch("app.pipeline.podcast_workers.get_db")
    @patch("app.pipeline.podcast_workers.content_to_domain")
    @patch("app.pipeline.podcast_workers.domain_to_content")
    @patch("app.pipeline.podcast_workers.get_whisper_local_service")
    @patch("app.pipeline.podcast_workers.Path.exists")
    @patch("app.pipeline.podcast_workers.open", create=True)
    def test_process_transcribe_task_success(
        self,
        mock_open,
        mock_exists,
        mock_get_service,
        mock_domain_to_content,
        mock_content_to_domain,
        mock_get_db,
        worker,
        mock_db_content,
        mock_domain_content,
    ):
        """Test successful transcription of audio file."""
        # Setup mocks
        mock_exists.return_value = True
        mock_db = MagicMock()
        mock_get_db.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = mock_db_content
        mock_content_to_domain.return_value = mock_domain_content

        # Mock whisper-local service
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.transcribe_audio.return_value = (
            "This is the transcribed text from the podcast.",
            "en",
        )
        worker.transcription_service = mock_service

        # Mock file operations
        mock_file = MagicMock()
        mock_open.return_value.__enter__.return_value = mock_file

        # Mock queue service
        worker.queue_service = MagicMock()

        # Execute
        result = worker.process_transcribe_task(1)

        # Assertions
        assert result is True
        mock_service.transcribe_audio.assert_called_once_with(Path("/path/to/test.mp3"))
        mock_file.write.assert_called_once_with("This is the transcribed text from the podcast.")
        worker.queue_service.enqueue.assert_called_once_with(TaskType.SUMMARIZE, content_id=1)

        # Verify domain_to_content was called to update DB
        mock_domain_to_content.assert_called_once()

        # Verify metadata was updated on the domain content
        assert (
            mock_domain_content.metadata["transcript"]
            == "This is the transcribed text from the podcast."
        )
        assert mock_domain_content.metadata["detected_language"] == "en"
        assert mock_domain_content.metadata["transcription_service"] == "whisper_local"

    @patch("app.pipeline.podcast_workers.get_db")
    def test_process_transcribe_task_content_not_found(self, mock_get_db, worker):
        """Test transcription when content is not found."""
        # Setup mocks
        mock_db = MagicMock()
        mock_get_db.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = None

        # Execute
        result = worker.process_transcribe_task(1)

        # Assertions
        assert result is False

    @patch("app.pipeline.podcast_workers.get_db")
    @patch("app.pipeline.podcast_workers.content_to_domain")
    @patch("app.pipeline.podcast_workers.Path.exists")
    def test_process_transcribe_task_file_not_found(
        self,
        mock_exists,
        mock_content_to_domain,
        mock_get_db,
        worker,
        mock_db_content,
        mock_domain_content,
    ):
        """Test transcription when audio file is not found."""
        # Setup mocks
        mock_exists.return_value = False
        mock_db = MagicMock()
        mock_get_db.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = mock_db_content
        mock_content_to_domain.return_value = mock_domain_content

        # Execute
        result = worker.process_transcribe_task(1)

        # Assertions
        assert result is False
        assert mock_db_content.status == ContentStatus.FAILED.value
        assert "Audio file not found" in mock_db_content.error_message

    @patch("app.pipeline.podcast_workers.get_db")
    @patch("app.pipeline.podcast_workers.content_to_domain")
    @patch("app.pipeline.podcast_workers.get_whisper_local_service")
    @patch("app.pipeline.podcast_workers.Path.exists")
    def test_process_transcribe_task_openai_error(
        self,
        mock_exists,
        mock_get_service,
        mock_content_to_domain,
        mock_get_db,
        worker,
        mock_db_content,
        mock_domain_content,
    ):
        """Test transcription when local service fails."""
        # Setup mocks
        mock_exists.return_value = True
        mock_db = MagicMock()
        mock_get_db.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = mock_db_content
        mock_content_to_domain.return_value = mock_domain_content

        # Mock local service error
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.transcribe_audio.side_effect = Exception("Transcription error")
        worker.transcription_service = mock_service

        # Execute
        result = worker.process_transcribe_task(1)

        # Assertions
        assert result is False
        assert mock_db_content.status == ContentStatus.FAILED.value
        assert "Transcription error" in mock_db_content.error_message
        assert mock_db_content.retry_count == 1

    def test_get_transcription_service_initialization(self, worker):
        """Test lazy initialization of transcription service."""
        assert worker.transcription_service is None

        with patch("app.pipeline.podcast_workers.get_whisper_local_service") as mock_get_service:
            mock_service = MagicMock()
            mock_get_service.return_value = mock_service

            worker._get_transcription_service()

            assert worker.transcription_service == mock_service
            mock_get_service.assert_called_once()

    def test_get_transcription_service_initialization_error(self, worker):
        """Test transcription service initialization error."""
        with patch("app.pipeline.podcast_workers.get_whisper_local_service") as mock_get_service:
            mock_get_service.side_effect = ValueError("No API key")

            with pytest.raises(ValueError, match="No API key"):
                worker._get_transcription_service()

    @patch("app.pipeline.podcast_workers.get_db")
    @patch("app.pipeline.podcast_workers.content_to_domain")
    @patch("app.pipeline.podcast_workers.domain_to_content")
    @patch("app.pipeline.podcast_workers.get_whisper_local_service")
    @patch("app.pipeline.podcast_workers.Path.exists")
    @patch("app.pipeline.podcast_workers.open", create=True)
    def test_process_transcribe_task_no_language_detected(
        self,
        mock_open,
        mock_exists,
        mock_get_service,
        mock_domain_to_content,
        mock_content_to_domain,
        mock_get_db,
        worker,
        mock_db_content,
        mock_domain_content,
    ):
        """Test transcription when no language is detected."""
        # Setup mocks
        mock_exists.return_value = True
        mock_db = MagicMock()
        mock_get_db.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.first.return_value = mock_db_content
        mock_content_to_domain.return_value = mock_domain_content

        # Mock local service returning no language
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.transcribe_audio.return_value = (
            "This is the transcribed text.",
            None,  # No language detected
        )
        worker.transcription_service = mock_service

        # Mock file operations
        mock_file = MagicMock()
        mock_open.return_value.__enter__.return_value = mock_file

        # Mock queue service
        worker.queue_service = MagicMock()

        # Execute
        result = worker.process_transcribe_task(1)

        # Assertions
        assert result is True
        assert "detected_language" not in mock_domain_content.metadata
        assert mock_domain_content.metadata["transcription_service"] == "whisper_local"

    def test_cleanup_service(self, worker):
        """Test cleanup of transcription service."""
        worker.transcription_service = MagicMock()

        worker.cleanup_service()

        assert worker.transcription_service is None
