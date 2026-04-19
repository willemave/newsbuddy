from __future__ import annotations


def test_audio_transcription_endpoint(client, monkeypatch):
    class FakeTranscriptionService:
        def transcribe_audio_from_buffer(self, audio_buffer, filename, *, user_id=None):
            assert audio_buffer.read() == b"audio-bytes"
            assert filename == "clip.m4a"
            assert user_id is not None
            return ("transcribed text", "en")

    monkeypatch.setattr(
        "app.routers.api.openai.get_openai_transcription_service",
        lambda: FakeTranscriptionService(),
    )

    response = client.post(
        "/api/openai/transcriptions",
        files={"file": ("clip.m4a", b"audio-bytes", "audio/m4a")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data == {"transcript": "transcribed text", "language": "en"}


def test_audio_transcription_endpoint_maps_unexpected_errors_to_bad_gateway(client, monkeypatch):
    class FakeTranscriptionService:
        def transcribe_audio_from_buffer(self, audio_buffer, filename, *, user_id=None):
            del audio_buffer, filename, user_id
            raise Exception("upstream connection failed")

    monkeypatch.setattr(
        "app.routers.api.openai.get_openai_transcription_service",
        lambda: FakeTranscriptionService(),
    )

    response = client.post(
        "/api/openai/transcriptions",
        files={"file": ("clip.m4a", b"audio-bytes", "audio/m4a")},
    )
    assert response.status_code == 502
    assert response.json() == {"detail": "upstream connection failed"}
