"""Tests for one-shot narration TTS."""

from __future__ import annotations

from threading import Barrier
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.db import VendorUsageRecord
from app.services.voice import narration_tts


def _elevenlabs_tts_settings() -> SimpleNamespace:
    return SimpleNamespace(
        elevenlabs_api_key="test-key",
        elevenlabs_tts_voice_id="fallback-voice",
        elevenlabs_podcast_host_voice_id="host-voice",
        elevenlabs_podcast_guest_voice_id="guest-voice",
        elevenlabs_narration_tts_model="eleven_flash_v2_5",
        elevenlabs_narration_tts_output_format="mp3_44100_128",
        elevenlabs_narration_tts_speed=1.0,
        elevenlabs_audio_episode_tts_max_workers=2,
    )


def _fake_elevenlabs(
    captured_calls: list[dict[str, object]],
    *,
    barrier: Barrier | None = None,
    captured_clients: list[object] | None = None,
):
    class FakeTextToSpeech:
        def convert(self, **kwargs):
            if barrier is not None:
                barrier.wait(timeout=1)
            captured_calls.append(kwargs)
            return iter([str(kwargs["text"]).encode()])

    class FakeElevenLabs:
        def __init__(self, api_key: str | None, httpx_client: object) -> None:
            self.api_key = api_key
            self.httpx_client = httpx_client
            self.text_to_speech = FakeTextToSpeech()
            if captured_clients is not None:
                captured_clients.append(self)

    return FakeElevenLabs


class FakeVoiceSettings:
    def __init__(self, *, speed: float) -> None:
        self.speed = speed


def test_content_narration_tts_forwards_elevenlabs_speech_settings(monkeypatch) -> None:
    """Content narration TTS should use the configured ElevenLabs speech settings."""

    captured_calls: list[dict[str, object]] = []
    monkeypatch.setattr(narration_tts, "ElevenLabs", _fake_elevenlabs(captured_calls))
    monkeypatch.setattr(narration_tts, "VoiceSettings", FakeVoiceSettings)
    monkeypatch.setattr(narration_tts, "find_spec", lambda _name: object())
    monkeypatch.setattr(
        narration_tts,
        "record_vendor_usage_out_of_band",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        narration_tts,
        "get_settings",
        _elevenlabs_tts_settings,
    )
    narration_tts._content_narration_tts_service = None

    audio = narration_tts.get_content_narration_tts_service().synthesize_mp3(text="Hello world")

    assert audio == b"Hello world"
    assert captured_calls == [
        {
            "model_id": "eleven_flash_v2_5",
            "voice_id": "fallback-voice",
            "text": "Hello world",
            "output_format": "mp3_44100_128",
            "voice_settings": captured_calls[0]["voice_settings"],
        }
    ]
    assert isinstance(captured_calls[0]["voice_settings"], FakeVoiceSettings)
    assert captured_calls[0]["voice_settings"].speed == 1.0


def test_content_narration_tts_records_vendor_usage(
    monkeypatch,
    db_session,
    vendor_usage_db,
    user_factory,
) -> None:
    """Content narration TTS should persist one ElevenLabs usage row."""
    del vendor_usage_db
    user = user_factory()

    monkeypatch.setattr(narration_tts, "ElevenLabs", _fake_elevenlabs([]))
    monkeypatch.setattr(narration_tts, "VoiceSettings", FakeVoiceSettings)
    monkeypatch.setattr(narration_tts, "find_spec", lambda _name: object())
    monkeypatch.setattr(
        narration_tts,
        "get_settings",
        _elevenlabs_tts_settings,
    )
    narration_tts._content_narration_tts_service = None

    audio = narration_tts.get_content_narration_tts_service().synthesize_mp3(
        text="Hello world",
        item_id=42,
        user_id=user.id,
    )

    assert audio == b"Hello world"
    row = db_session.query(VendorUsageRecord).one()
    assert row.provider == "elevenlabs"
    assert row.model == "eleven_flash_v2_5"
    assert row.feature == "narration_tts"
    assert row.user_id == user.id
    assert row.request_count == 1
    assert row.resource_count == len("Hello world")
    assert row.metadata_json["target_id"] == 42


def test_dialogue_tts_parallelizes_turn_synthesis(monkeypatch) -> None:
    """Podcast-style audio should synthesize turns concurrently through one client."""

    captured_calls: list[dict[str, object]] = []
    captured_clients: list[Any] = []
    captured_chunks: list[bytes] = []
    captured_usage: list[dict[str, object]] = []

    monkeypatch.setattr(
        narration_tts,
        "ElevenLabs",
        _fake_elevenlabs(
            captured_calls,
            barrier=Barrier(2),
            captured_clients=captured_clients,
        ),
    )
    monkeypatch.setattr(narration_tts, "VoiceSettings", FakeVoiceSettings)
    monkeypatch.setattr(narration_tts, "find_spec", lambda _name: object())
    monkeypatch.setattr(
        narration_tts,
        "record_vendor_usage_out_of_band",
        lambda **kwargs: captured_usage.append(kwargs),
    )

    def fake_stitch(_self, chunks: list[bytes], **_kwargs) -> tuple[bytes, float]:
        captured_chunks.extend(chunks)
        return b"".join(chunks), 0.5

    monkeypatch.setattr(
        narration_tts.ContentNarrationTtsService,
        "_stitch_mp3_chunks",
        fake_stitch,
    )
    monkeypatch.setattr(
        narration_tts,
        "get_settings",
        _elevenlabs_tts_settings,
    )
    narration_tts._content_narration_tts_service = None

    audio = narration_tts.get_content_narration_tts_service().synthesize_dialogue_mp3(
        turns=[
            {"speaker": "host", "text": "Welcome."},
            {"speaker": "expert", "text": "Here is the analysis."},
        ]
    )

    assert audio == b"Welcome.Here is the analysis."
    assert captured_chunks == [b"Welcome.", b"Here is the analysis."]
    calls_by_text = {str(call["text"]): call for call in captured_calls}
    assert calls_by_text["Welcome."]["voice_id"] == "host-voice"
    assert calls_by_text["Here is the analysis."]["voice_id"] == "guest-voice"
    assert {call["model_id"] for call in captured_calls} == {"eleven_flash_v2_5"}
    assert {call["output_format"] for call in captured_calls} == {"mp3_44100_128"}
    for call in captured_calls:
        voice_settings = call["voice_settings"]
        assert isinstance(voice_settings, FakeVoiceSettings)
        assert voice_settings.speed == 1.0
    assert len(captured_clients) == 1
    assert captured_clients[0].httpx_client.is_closed is True
    assert captured_usage[0]["usage"] == {
        "request_count": 2,
        "resource_count": len("Welcome.") + len("Here is the analysis."),
    }


def test_content_narration_tts_requires_elevenlabs_api_key(monkeypatch) -> None:
    settings = _elevenlabs_tts_settings()
    settings.elevenlabs_api_key = None
    monkeypatch.setattr(narration_tts, "get_settings", lambda: settings)

    service = narration_tts.ContentNarrationTtsService()

    with pytest.raises(
        narration_tts.NarrationTtsConfigurationError,
        match="ElevenLabs API key is not configured",
    ):
        service.synthesize_mp3(text="Hello world")


def test_content_narration_tts_rejects_empty_audio(monkeypatch) -> None:
    class EmptyTextToSpeech:
        def convert(self, **_kwargs):
            return iter([])

    class EmptyElevenLabs:
        def __init__(self, api_key: str | None, httpx_client: object) -> None:
            self.text_to_speech = EmptyTextToSpeech()

    monkeypatch.setattr(narration_tts, "ElevenLabs", EmptyElevenLabs)
    monkeypatch.setattr(narration_tts, "VoiceSettings", FakeVoiceSettings)
    monkeypatch.setattr(narration_tts, "find_spec", lambda _name: object())
    monkeypatch.setattr(narration_tts, "get_settings", _elevenlabs_tts_settings)
    monkeypatch.setattr(narration_tts, "record_vendor_usage_out_of_band", lambda **_kwargs: None)

    with pytest.raises(RuntimeError, match="Content narration audio was empty"):
        narration_tts.ContentNarrationTtsService().synthesize_mp3(text="Hello world")


def test_dialogue_tts_chunks_long_turns_losslessly_at_provider_boundary() -> None:
    target = narration_tts.TTS_CHUNK_TARGET_CHARS
    assert target < narration_tts.ELEVENLABS_FLASH_MAX_INPUT_CHARS
    natural_text = (
        "A complete sentence with natural pacing.\n\n"
        "A follow-up keeps its pause,\t  including repeated spaces. " * 100
    ).strip()
    pathological_token = "x" * (target * 2 + 17)

    provider_turns = narration_tts._normalize_dialogue_turns(
        [
            {"speaker": "host", "text": natural_text},
            {"speaker": "expert", "text": pathological_token},
        ]
    )

    assert provider_turns
    assert all(len(turn["text"]) <= target for turn in provider_turns)
    host_chunks = [turn["text"] for turn in provider_turns if turn["speaker"] == "host"]
    guest_chunks = [turn["text"] for turn in provider_turns if turn["speaker"] == "guest"]
    assert "".join(host_chunks) == natural_text
    assert "".join(guest_chunks) == pathological_token


def test_dialogue_tts_splits_oversized_turn_before_elevenlabs(monkeypatch) -> None:
    target = narration_tts.TTS_CHUNK_TARGET_CHARS
    text = "x" * (target * 2 + 17)
    captured_calls: list[dict[str, object]] = []
    captured_chunks: list[bytes] = []

    monkeypatch.setattr(narration_tts, "ElevenLabs", _fake_elevenlabs(captured_calls))
    monkeypatch.setattr(narration_tts, "VoiceSettings", FakeVoiceSettings)
    monkeypatch.setattr(narration_tts, "find_spec", lambda _name: object())
    monkeypatch.setattr(narration_tts, "get_settings", _elevenlabs_tts_settings)
    monkeypatch.setattr(
        narration_tts,
        "record_vendor_usage_out_of_band",
        lambda **_kwargs: None,
    )

    def fake_stitch(_self, chunks: list[bytes], **_kwargs) -> tuple[bytes, float]:
        captured_chunks.extend(chunks)
        return b"".join(chunks), 0.5

    monkeypatch.setattr(
        narration_tts.ContentNarrationTtsService,
        "_stitch_mp3_chunks",
        fake_stitch,
    )

    audio = narration_tts.ContentNarrationTtsService().synthesize_dialogue_mp3(
        turns=[{"speaker": "host", "text": text}]
    )

    provider_inputs = [str(call["text"]) for call in captured_calls]
    expected_chunks = [
        turn["text"]
        for turn in narration_tts._normalize_dialogue_turns([{"speaker": "host", "text": text}])
    ]
    assert len(provider_inputs) == 3
    assert all(len(chunk) <= target for chunk in provider_inputs)
    assert sorted(provider_inputs, key=len) == sorted(expected_chunks, key=len)
    assert captured_chunks == [chunk.encode() for chunk in expected_chunks]
    assert audio == text.encode()


def test_content_narration_tts_chunks_long_input_at_provider_boundary(monkeypatch) -> None:
    target = narration_tts.TTS_CHUNK_TARGET_CHARS
    text = ("A complete sentence with natural pacing. " * 1_200).strip()
    captured_calls: list[dict[str, object]] = []
    captured_usage: list[dict[str, object]] = []

    monkeypatch.setattr(narration_tts, "ElevenLabs", _fake_elevenlabs(captured_calls))
    monkeypatch.setattr(narration_tts, "VoiceSettings", FakeVoiceSettings)
    monkeypatch.setattr(narration_tts, "find_spec", lambda _name: object())
    monkeypatch.setattr(narration_tts, "get_settings", _elevenlabs_tts_settings)
    monkeypatch.setattr(
        narration_tts,
        "record_vendor_usage_out_of_band",
        lambda **kwargs: captured_usage.append(kwargs),
    )
    monkeypatch.setattr(
        narration_tts.ContentNarrationTtsService,
        "_stitch_mp3_chunks",
        lambda _self, chunks, **_kwargs: (b"".join(chunks), 0.5),
    )

    audio = narration_tts.ContentNarrationTtsService().synthesize_mp3(text=text)

    provider_inputs = [str(call["text"]) for call in captured_calls]
    assert len(provider_inputs) > 1
    assert all(len(chunk) <= target for chunk in provider_inputs)
    assert all(
        len(chunk) < narration_tts.ELEVENLABS_FLASH_MAX_INPUT_CHARS for chunk in provider_inputs
    )
    assert "".join(provider_inputs) == text
    assert audio == text.encode()
    assert captured_usage[0]["usage"] == {
        "request_count": len(provider_inputs),
        "resource_count": len(text),
    }


def test_dialogue_tts_stitches_turns_with_ffmpeg(monkeypatch) -> None:
    """Parallel dialogue turn audio should be re-encoded into one valid MP3 asset."""

    service = narration_tts.ContentNarrationTtsService.__new__(
        narration_tts.ContentNarrationTtsService
    )
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["timeout"] = kwargs["timeout"]
        output_path = cmd[-1]
        with open(output_path, "wb") as output_file:
            output_file.write(b"stitched-mp3")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(narration_tts.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(narration_tts.subprocess, "run", fake_run)

    audio, duration_ms = service._stitch_mp3_chunks(
        [b"turn-one", b"turn-two", b"turn-three", b"turn-four"],
        item_id=42,
        user_id=7,
    )

    assert audio == b"stitched-mp3"
    assert duration_ms >= 0
    command = captured["cmd"]
    assert isinstance(command, list)
    assert command[:10] == [
        "/usr/bin/ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
    ]
    assert command[-5:-1] == ["-codec:a", "libmp3lame", "-b:a", "128k"]
    assert captured["timeout"] == 60


def test_dialogue_tts_missing_ffmpeg_is_typed_configuration_failure(monkeypatch) -> None:
    service = narration_tts.ContentNarrationTtsService.__new__(
        narration_tts.ContentNarrationTtsService
    )
    monkeypatch.setattr(narration_tts.shutil, "which", lambda _name: None)

    with pytest.raises(
        narration_tts.NarrationTtsConfigurationError,
        match="ffmpeg is required",
    ):
        service._stitch_mp3_chunks([b"turn-one", b"turn-two"])
