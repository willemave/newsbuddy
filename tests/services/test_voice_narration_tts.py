"""Tests for one-shot narration TTS."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

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


def test_dialogue_tts_uses_text_to_dialogue(monkeypatch) -> None:
    """Podcast-style audio should use ElevenLabs dialogue input turns."""

    captured_kwargs: dict[str, object] = {}

    class FakeTextToDialogue:
        def convert(self, **kwargs):
            captured_kwargs.update(kwargs)
            return iter([b"dialogue"])

    class FakeElevenLabs:
        def __init__(self, api_key: str | None) -> None:
            self.api_key = api_key
            self.text_to_dialogue = FakeTextToDialogue()

    class FakeDialogueInput:
        def __init__(self, *, text: str, voice_id: str) -> None:
            self.text = text
            self.voice_id = voice_id

    monkeypatch.setattr(narration_tts, "ElevenLabs", FakeElevenLabs)
    monkeypatch.setattr(narration_tts, "DialogueInput", FakeDialogueInput)
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
            elevenlabs_tts_voice_id="fallback-voice",
            elevenlabs_podcast_host_voice_id="host-voice",
            elevenlabs_podcast_guest_voice_id="guest-voice",
            elevenlabs_narration_tts_model="eleven_turbo_v2_5",
            elevenlabs_dialogue_tts_model="eleven_v3",
            elevenlabs_narration_tts_output_format="mp3_44100_128",
            elevenlabs_narration_tts_speed=1.0,
        ),
    )
    narration_tts._content_narration_tts_service = None

    audio = narration_tts.get_content_narration_tts_service().synthesize_dialogue_mp3(
        turns=[
            {"speaker": "host", "text": "Welcome."},
            {"speaker": "expert", "text": "Here is the analysis."},
        ]
    )

    assert audio == b"dialogue"
    inputs = cast(list[FakeDialogueInput], captured_kwargs["inputs"])
    assert [input.voice_id for input in inputs] == ["host-voice", "guest-voice"]
    assert [input.text for input in inputs] == ["Welcome.", "Here is the analysis."]
    assert captured_kwargs["model_id"] == "eleven_v3"
