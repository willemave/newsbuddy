from __future__ import annotations


def test_realtime_token_endpoint(client, monkeypatch):
    def fake_create_secret(*, locale=None):
        return ("test-token", 1234567890, "gpt-realtime")

    monkeypatch.setattr(
        "app.services.openai_realtime.create_transcription_session_token", fake_create_secret
    )

    response = client.post("/api/openai/realtime/token")
    assert response.status_code == 200
    data = response.json()
    assert data["token"] == "test-token"
    assert data["expires_at"] == 1234567890
    assert data["model"] == "gpt-realtime"
    assert data["session_type"] == "transcription"


def test_audio_transcription_endpoint(client, monkeypatch):
    class FakeTranscriptionService:
        def transcribe_audio_from_buffer(self, audio_buffer, filename):
            assert audio_buffer.read() == b"audio-bytes"
            assert filename == "clip.m4a"
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
        def transcribe_audio_from_buffer(self, audio_buffer, filename):
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
