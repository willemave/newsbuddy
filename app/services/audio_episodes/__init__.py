"""Public facade for on-demand audio episode services."""

from app.services.audio_episode_errors import AudioEpisodeInputError
from app.services.audio_episode_kinds import (
    AUDIO_EPISODE_MODEL,
    BRIEFING_NARRATION_KIND,
    CONTENT_COUNCIL_DISCUSSION_KIND,
    CUSTOM_NARRATION_KIND,
    FAST_NEWS_DIGEST_KIND,
    NEWS_ITEM_DISCUSSION_KIND,
)
from app.services.audio_episodes.creation import (
    FAST_NEWS_LIMIT,
    create_content_council_episode,
    create_custom_narration_episode,
    create_fast_news_digest_episode,
    create_news_item_discussion_episode,
    enqueue_audio_episode_generation,
    get_user_audio_episode,
    list_custom_narration_episodes,
)
from app.services.audio_episodes.generation import (
    AUDIO_EPISODE_PROCESSING_STALE_AFTER,
    AudioEpisodeNotFoundError,
    audio_episode_file_path,
    finalize_audio_episode_failure,
    generate_audio_episode,
    is_audio_episode_processing_stale,
)
from app.services.audio_episodes.presentation import (
    commit_audio_episode_delivery,
    mark_audio_episode_sources_read_on_play,
    present_audio_episode,
)
from app.services.audio_episodes.scripting import (
    SCRIPT_TIMEOUT_SECONDS,
    AudioEpisodeScript,
    AudioEpisodeScriptGeneration,
    AudioEpisodeTurn,
)
from app.services.audio_episodes.shared import (
    PROMPT_VERSION,
    PUBLIC_AUDIO_EPISODE_ERROR_MESSAGE,
)
from app.services.audio_episodes.streaming import (
    AUDIO_EPISODE_FILE_CHUNK_SIZE,
    AUDIO_EPISODE_FOLLOW_POLL_SECONDS,
    AUDIO_EPISODE_FOLLOW_TIMEOUT_SECONDS,
    AudioEpisodeAlreadyProcessingError,
    follow_audio_episode_stream_chunks,
)

__all__ = [
    "AUDIO_EPISODE_FILE_CHUNK_SIZE",
    "AUDIO_EPISODE_FOLLOW_POLL_SECONDS",
    "AUDIO_EPISODE_FOLLOW_TIMEOUT_SECONDS",
    "AUDIO_EPISODE_MODEL",
    "AUDIO_EPISODE_PROCESSING_STALE_AFTER",
    "BRIEFING_NARRATION_KIND",
    "CONTENT_COUNCIL_DISCUSSION_KIND",
    "CUSTOM_NARRATION_KIND",
    "FAST_NEWS_DIGEST_KIND",
    "FAST_NEWS_LIMIT",
    "NEWS_ITEM_DISCUSSION_KIND",
    "PROMPT_VERSION",
    "PUBLIC_AUDIO_EPISODE_ERROR_MESSAGE",
    "SCRIPT_TIMEOUT_SECONDS",
    "AudioEpisodeAlreadyProcessingError",
    "AudioEpisodeInputError",
    "AudioEpisodeNotFoundError",
    "AudioEpisodeScript",
    "AudioEpisodeScriptGeneration",
    "AudioEpisodeTurn",
    "audio_episode_file_path",
    "commit_audio_episode_delivery",
    "create_content_council_episode",
    "create_custom_narration_episode",
    "create_fast_news_digest_episode",
    "create_news_item_discussion_episode",
    "enqueue_audio_episode_generation",
    "finalize_audio_episode_failure",
    "follow_audio_episode_stream_chunks",
    "generate_audio_episode",
    "get_user_audio_episode",
    "is_audio_episode_processing_stale",
    "list_custom_narration_episodes",
    "mark_audio_episode_sources_read_on_play",
    "present_audio_episode",
]
