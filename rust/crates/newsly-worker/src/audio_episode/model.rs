use chrono::{DateTime, Utc};
use newsly_db::{AudioEpisodeScriptUsage, AudioEpisodeTtsUsage};
use newsly_providers::AudioEpisodeScript;
use serde_json::Value;

#[derive(Debug, Clone)]
pub(super) struct PreparedAudioEpisodeAttempt {
    pub(super) task_id: i64,
    pub(super) retry_count: i32,
    pub(super) user_id: i64,
    pub(super) audio_episode_id: i64,
    pub(super) prepared_started_at: DateTime<Utc>,
    pub(super) kind: String,
    pub(super) fallback_title: String,
    pub(super) source_content_id: Option<i64>,
    pub(super) source_snapshot: Value,
    pub(super) existing_script: Option<Value>,
    pub(super) existing_script_text: Option<String>,
    pub(super) existing_model: Option<String>,
}

#[derive(Debug, Clone)]
pub(super) struct PreparedScript {
    pub(super) script: AudioEpisodeScript,
    pub(super) script_json: Value,
    pub(super) script_text: String,
    pub(super) model: String,
    pub(super) duration_seconds: i32,
    pub(super) usage: Option<AudioEpisodeScriptUsage>,
}

#[derive(Debug, Clone)]
pub(super) enum AudioEpisodeMutation {
    Complete {
        script: PreparedScript,
        audio_storage_path: String,
        tts_usage: AudioEpisodeTtsUsage,
    },
    Failed {
        error_message: String,
        retry_scheduled: bool,
        generated_script: Option<PreparedScript>,
    },
}

#[derive(Debug, Clone)]
pub(super) struct AudioEpisodeFinalizationPlan {
    pub(super) attempt: PreparedAudioEpisodeAttempt,
    pub(super) mutation: AudioEpisodeMutation,
}
