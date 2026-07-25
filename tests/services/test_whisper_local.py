"""Tests for the local faster-whisper transcription service."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.services import whisper_local
from app.services.whisper_local import WhisperLocalTranscriptionService


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    path = tmp_path / "episode.mp3"
    path.write_bytes(b"not really audio")
    return path


def _service(*, device: str = "cpu") -> WhisperLocalTranscriptionService:
    with patch.object(WhisperLocalTranscriptionService, "_get_device", return_value=device):
        return WhisperLocalTranscriptionService()


def test_cpu_uses_int8_and_cuda_uses_float16() -> None:
    """int8 is what makes CPU transcription affordable; GPUs get float16."""
    assert _service(device="cpu").compute_type == "int8"
    assert _service(device="cuda").compute_type == "float16"


def test_mps_falls_back_to_cpu() -> None:
    """CTranslate2 has no MPS backend, so an mps setting must not reach it."""
    with patch.object(whisper_local, "settings", SimpleNamespace(whisper_device="mps")):
        assert WhisperLocalTranscriptionService()._get_device() == "cpu"


def test_transcribe_joins_segments_and_reports_language(audio_file: Path) -> None:
    """faster-whisper yields segments lazily; the transcript is their join."""
    service = _service()
    service.model = Mock()
    service.model.transcribe.return_value = (
        iter(
            [
                SimpleNamespace(text=" Hello there."),
                SimpleNamespace(text=" Second segment."),
            ]
        ),
        SimpleNamespace(language="en"),
    )

    transcript, language = service.transcribe_audio(audio_file)

    assert transcript == "Hello there. Second segment."
    assert language == "en"


def test_transcribe_rejects_a_missing_file(tmp_path: Path) -> None:
    service = _service()
    service.model = Mock()

    with pytest.raises(FileNotFoundError):
        service.transcribe_audio(tmp_path / "absent.mp3")


def test_transcription_runs_one_at_a_time(audio_file: Path) -> None:
    """Media workers run several claim threads, but only one may transcribe."""
    service = _service()
    service.model = Mock()
    concurrent = 0
    peak_concurrent = 0
    counter_lock = threading.Lock()
    inside = threading.Event()

    def fake_transcribe(*_args, **_kwargs):
        nonlocal concurrent, peak_concurrent
        with counter_lock:
            concurrent += 1
            peak_concurrent = max(peak_concurrent, concurrent)
        inside.set()
        # Hold the model long enough that a second thread would overlap if it could.
        threading.Event().wait(0.05)
        with counter_lock:
            concurrent -= 1
        return iter([SimpleNamespace(text="ok")]), SimpleNamespace(language="en")

    service.model.transcribe.side_effect = fake_transcribe

    threads = [
        threading.Thread(target=service.transcribe_audio, args=(audio_file,)) for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert inside.is_set()
    assert peak_concurrent == 1
