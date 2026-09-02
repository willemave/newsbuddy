use std::env;
use std::ffi::OsStr;
use std::path::Path;

use anyhow::{Result, bail};

#[path = "bin/agent_data_worker.rs"]
mod agent_data_worker;
#[path = "bin/audio_episode_worker.rs"]
mod audio_episode_worker;
#[path = "bin/briefing_refresh_worker.rs"]
mod briefing_refresh_worker;
#[path = "bin/chat_worker.rs"]
mod chat_worker;
#[path = "bin/content_worker.rs"]
mod content_worker;
#[path = "bin/discussion_worker.rs"]
mod discussion_worker;
#[path = "bin/feed_backfill_worker.rs"]
mod feed_backfill_worker;
#[path = "bin/feed_discovery_worker.rs"]
mod feed_discovery_worker;
#[path = "bin/image_worker.rs"]
mod image_worker;
#[path = "bin/media_worker.rs"]
mod media_worker;
#[path = "bin/news_item_worker.rs"]
mod news_item_worker;
#[path = "bin/onboarding_discovery_worker.rs"]
mod onboarding_discovery_worker;
#[path = "bin/run_llm_task_worker.rs"]
mod run_llm_task_worker;
#[path = "bin/scrape_worker.rs"]
mod scrape_worker;
#[path = "bin/summarization_worker.rs"]
mod summarization_worker;
#[path = "bin/x_sync_worker.rs"]
mod x_sync_worker;

const PROCESS_OVERRIDE_ENV: &str = "NEWSLY_WORKER_PROCESS";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum WorkerProcess {
    Content,
    Media,
    AudioEpisode,
    Image,
    Discussion,
    NewsItem,
    Scrape,
    Summarization,
    XSync,
    AgentData,
    FeedBackfill,
    FeedDiscovery,
    OnboardingDiscovery,
    BriefingRefresh,
    Chat,
    RunLlmTask,
}

impl WorkerProcess {
    fn detect() -> Result<Self> {
        if let Some(value) = env::var_os(PROCESS_OVERRIDE_ENV) {
            return Self::from_name(&value);
        }

        let executable = env::args_os().next().unwrap_or_default();
        let name = Path::new(&executable)
            .file_name()
            .unwrap_or(executable.as_os_str());
        Self::from_name(name)
    }

    fn from_name(name: &OsStr) -> Result<Self> {
        let Some(name) = name.to_str() else {
            bail!("worker process name must be valid UTF-8");
        };
        match name {
            "newsly-worker" | "newsly-content-worker" | "content" => Ok(Self::Content),
            "newsly-media-worker" | "media" => Ok(Self::Media),
            "newsly-audio-worker" | "audio_episode" => Ok(Self::AudioEpisode),
            "newsly-image-worker" | "image" => Ok(Self::Image),
            "newsly-discussion-worker" | "discussion" => Ok(Self::Discussion),
            "newsly-news-item-worker" | "news_item" => Ok(Self::NewsItem),
            "newsly-scrape-worker" | "scrape" => Ok(Self::Scrape),
            "newsly-summarization-worker" | "summarization" => Ok(Self::Summarization),
            "newsly-x-sync-worker" | "x_sync" => Ok(Self::XSync),
            "newsly-agent-data-worker" | "agent_data" => Ok(Self::AgentData),
            "newsly-feed-backfill-worker" | "feed_backfill" => Ok(Self::FeedBackfill),
            "newsly-feed-discovery-worker" | "feed_discovery" => Ok(Self::FeedDiscovery),
            "newsly-onboarding-discovery-worker" | "onboarding_discovery" => {
                Ok(Self::OnboardingDiscovery)
            }
            "newsly-briefing-refresh-worker" | "briefing_refresh" => Ok(Self::BriefingRefresh),
            "newsly-chat-worker" | "chat" => Ok(Self::Chat),
            "newsly-run-llm-task-worker" | "run_llm_task" => Ok(Self::RunLlmTask),
            _ => bail!(
                "unsupported worker process {name:?}; set {PROCESS_OVERRIDE_ENV} to a known worker name"
            ),
        }
    }

    fn run(self) -> Result<()> {
        match self {
            Self::Content => content_worker::main(),
            Self::Media => media_worker::main(),
            Self::AudioEpisode => audio_episode_worker::main(),
            Self::Image => image_worker::main(),
            Self::Discussion => discussion_worker::main(),
            Self::NewsItem => news_item_worker::main(),
            Self::Scrape => scrape_worker::main(),
            Self::Summarization => summarization_worker::main(),
            Self::XSync => x_sync_worker::main(),
            Self::AgentData => agent_data_worker::main(),
            Self::FeedBackfill => feed_backfill_worker::main(),
            Self::FeedDiscovery => feed_discovery_worker::main(),
            Self::OnboardingDiscovery => onboarding_discovery_worker::main(),
            Self::BriefingRefresh => briefing_refresh_worker::main(),
            Self::Chat => chat_worker::main(),
            Self::RunLlmTask => run_llm_task_worker::main(),
        }
    }
}

fn main() -> Result<()> {
    WorkerProcess::detect()?.run()
}

#[cfg(test)]
mod tests {
    use super::WorkerProcess;
    use std::ffi::OsStr;

    #[test]
    fn production_aliases_select_the_expected_processes() {
        let cases = [
            ("newsly-content-worker", WorkerProcess::Content),
            ("newsly-media-worker", WorkerProcess::Media),
            ("newsly-audio-worker", WorkerProcess::AudioEpisode),
            ("newsly-image-worker", WorkerProcess::Image),
            ("newsly-discussion-worker", WorkerProcess::Discussion),
            ("newsly-news-item-worker", WorkerProcess::NewsItem),
            ("newsly-scrape-worker", WorkerProcess::Scrape),
            ("newsly-summarization-worker", WorkerProcess::Summarization),
            ("newsly-x-sync-worker", WorkerProcess::XSync),
            ("newsly-agent-data-worker", WorkerProcess::AgentData),
            ("newsly-feed-backfill-worker", WorkerProcess::FeedBackfill),
            ("newsly-feed-discovery-worker", WorkerProcess::FeedDiscovery),
            (
                "newsly-onboarding-discovery-worker",
                WorkerProcess::OnboardingDiscovery,
            ),
            (
                "newsly-briefing-refresh-worker",
                WorkerProcess::BriefingRefresh,
            ),
            ("newsly-chat-worker", WorkerProcess::Chat),
            ("newsly-run-llm-task-worker", WorkerProcess::RunLlmTask),
        ];

        for (name, expected) in cases {
            assert_eq!(
                WorkerProcess::from_name(OsStr::new(name)).unwrap(),
                expected
            );
        }
    }

    #[test]
    fn unknown_process_name_fails_closed() {
        let error = WorkerProcess::from_name(OsStr::new("newsly-unknown-worker")).unwrap_err();
        assert!(error.to_string().contains("unsupported worker process"));
    }
}
