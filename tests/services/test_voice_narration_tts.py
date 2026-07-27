"""Tests for one-shot narration TTS."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.db import VendorUsageRecord
from app.services.voice import narration_tts


def test_content_narration_tts_sets_speed_voice_setting(monkeypatch) -> None:
    """Content narration TTS should forward the configured speed to ElevenLabs voice settings."""

    captured_kwargs: dict[str, object] = {}

    class FakeTextToSpeech:
        def convert(self, **kwargs):
            captured_kwargs.update(kwargs)
            return iter([b"chunk"])

    class FakeElevenLabs:
        def __init__(self, api_key: str | None) -> None:
            self.api_key = api_key
            self.text_to_speech = FakeTextToSpeech()

    class FakeVoiceSettings:
        def __init__(self, *, speed: float) -> None:
            self.speed = speed

    monkeypatch.setattr(narration_tts, "ElevenLabs", FakeElevenLabs)
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
        lambda: SimpleNamespace(
            elevenlabs_api_key="test-key",
            elevenlabs_tts_voice_id="voice-id",
            elevenlabs_narration_tts_model="eleven_turbo_v2_5",
            elevenlabs_narration_tts_output_format="mp3_44100_128",
            elevenlabs_narration_tts_speed=1.0,
        ),
    )
    narration_tts._content_narration_tts_service = None

    audio = narration_tts.get_content_narration_tts_service().synthesize_mp3(text="Hello world")

    assert audio == b"chunk"
    voice_settings = captured_kwargs["voice_settings"]
    assert isinstance(voice_settings, FakeVoiceSettings)
    assert voice_settings.speed == 1.0


def test_content_narration_tts_records_vendor_usage(
    monkeypatch,
    db_session,
    vendor_usage_db,
) -> None:
    """Content narration TTS should persist one ElevenLabs usage row."""
    del vendor_usage_db

    class FakeTextToSpeech:
        def convert(self, **kwargs):
            del kwargs
            return iter([b"chunk"])

    class FakeElevenLabs:
        def __init__(self, api_key: str | None) -> None:
            self.api_key = api_key
            self.text_to_speech = FakeTextToSpeech()

    class FakeVoiceSettings:
        def __init__(self, *, speed: float) -> None:
            self.speed = speed

    monkeypatch.setattr(narration_tts, "ElevenLabs", FakeElevenLabs)
    monkeypatch.setattr(narration_tts, "VoiceSettings", FakeVoiceSettings)
    monkeypatch.setattr(narration_tts, "find_spec", lambda _name: object())
    monkeypatch.setattr(
        narration_tts,
        "get_settings",
        lambda: SimpleNamespace(
            elevenlabs_api_key="test-key",
            elevenlabs_tts_voice_id="voice-id",
            elevenlabs_narration_tts_model="eleven_turbo_v2_5",
            elevenlabs_narration_tts_output_format="mp3_44100_128",
            elevenlabs_narration_tts_speed=1.0,
        ),
    )
    narration_tts._content_narration_tts_service = None

    audio = narration_tts.get_content_narration_tts_service().synthesize_mp3(
        text="Hello world",
        item_id=42,
        user_id=7,
    )

    assert audio == b"chunk"
    row = db_session.query(VendorUsageRecord).one()
    assert row.provider == "elevenlabs"
    assert row.feature == "narration_tts"
    assert row.user_id == 7
    assert row.request_count == 1
    assert row.metadata_json["target_id"] == 42


def test_dialogue_tts_parallelizes_turn_synthesis(monkeypatch) -> None:
    """Podcast-style audio should synthesize turns independently with the fast TTS model."""

    captured_calls: list[dict[str, object]] = []
    captured_chunks: list[bytes] = []

    class FakeTextToSpeech:
        def convert(self, **kwargs):
            captured_calls.append(kwargs)
            return iter([str(kwargs["text"]).encode()])

    class FakeElevenLabs:
        def __init__(self, api_key: str | None) -> None:
            self.api_key = api_key
            self.text_to_speech = FakeTextToSpeech()

    class FakeVoiceSettings:
        def __init__(self, *, speed: float) -> None:
            self.speed = speed

    monkeypatch.setattr(narration_tts, "ElevenLabs", FakeElevenLabs)
    monkeypatch.setattr(narration_tts, "VoiceSettings", FakeVoiceSettings)
    monkeypatch.setattr(narration_tts, "find_spec", lambda _name: object())
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
        "_stitch_dialogue_turns_mp3",
        fake_stitch,
    )
    monkeypatch.setattr(
        narration_tts,
        "get_settings",
        lambda: SimpleNamespace(
            elevenlabs_api_key="test-key",
            elevenlabs_tts_voice_id="fallback-voice",
            elevenlabs_podcast_host_voice_id="host-voice",
            elevenlabs_podcast_guest_voice_id="guest-voice",
            elevenlabs_narration_tts_model="eleven_turbo_v2_5",
            elevenlabs_narration_tts_output_format="mp3_44100_128",
            elevenlabs_narration_tts_speed=1.0,
            elevenlabs_audio_episode_tts_max_workers=2,
        ),
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
    assert {call["model_id"] for call in captured_calls} == {"eleven_turbo_v2_5"}
    assert {call["output_format"] for call in captured_calls} == {"mp3_44100_128"}
    for call in captured_calls:
        voice_settings = call["voice_settings"]
        assert isinstance(voice_settings, FakeVoiceSettings)
        assert voice_settings.speed == 1.0


def test_dialogue_tts_chunks_long_turns_losslessly_at_provider_boundary() -> None:
    target = narration_tts.DIALOGUE_TTS_CHUNK_TARGET_CHARS
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


def test_dialogue_tts_stitches_turns_with_ffmpeg(monkeypatch) -> None:
    """Parallel dialogue turn audio should be re-encoded into one valid MP3 asset."""

    service = narration_tts.ContentNarrationTtsService.__new__(
        narration_tts.ContentNarrationTtsService
    )
    captured_cmd: dict[str, list[str]] = {}

    def fake_run(cmd, **kwargs):
        del kwargs
        captured_cmd["cmd"] = cmd
        output_path = cmd[-1]
        with open(output_path, "wb") as output_file:
            output_file.write(b"stitched-mp3")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(narration_tts.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(narration_tts.subprocess, "run", fake_run)

    audio, duration_ms = service._stitch_dialogue_turns_mp3(
        [b"turn-one", b"turn-two"],
        item_id=42,
        user_id=7,
    )

    assert audio == b"stitched-mp3"
    assert duration_ms >= 0
    assert captured_cmd["cmd"][:10] == [
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
    assert captured_cmd["cmd"][-5:-1] == ["-codec:a", "libmp3lame", "-b:a", "128k"]


def test_dialogue_tts_missing_ffmpeg_is_typed_configuration_failure(monkeypatch) -> None:
    service = narration_tts.ContentNarrationTtsService.__new__(
        narration_tts.ContentNarrationTtsService
    )
    monkeypatch.setattr(narration_tts.shutil, "which", lambda _name: None)

    with pytest.raises(
        narration_tts.NarrationTtsConfigurationError,
        match="ffmpeg is required",
    ):
        service._stitch_dialogue_turns_mp3([b"turn-one", b"turn-two"])
