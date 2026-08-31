use std::env;

use newsly_db::BriefingRefreshConfig;
use thiserror::Error;

const DEFAULT_MASTHEAD_TITLE: &str = "The Unread Times";

#[derive(Debug, Clone, PartialEq)]
pub struct BriefingRefreshWorkerConfig {
    pub repository: BriefingRefreshConfig,
    pub compose_parallelism: usize,
    pub embedding_batch_size: usize,
    pub event_similarity: f64,
    pub max_figures_deep: usize,
    pub max_compose_attempts: usize,
}

impl BriefingRefreshWorkerConfig {
    pub fn from_env() -> Result<Self, BriefingRefreshWorkerConfigError> {
        let config = Self {
            repository: BriefingRefreshConfig {
                masthead_title: env::var("BRIEFING_MASTHEAD_TITLE")
                    .unwrap_or_else(|_| DEFAULT_MASTHEAD_TITLE.to_owned()),
                window_min: parse_usize("BRIEFING_WINDOW_MIN", 3)?,
                news_window_max: parse_usize("BRIEFING_NEWS_WINDOW_MAX", 4)?,
                new_lens_min_items: parse_usize("BRIEFING_NEW_LENS_MIN_ITEMS", 3)?,
                pending_max_age_seconds: parse_i64("BRIEFING_PENDING_MAX_AGE_SECONDS", 1_500)?,
                max_news_lenses: parse_usize("BRIEFING_MAX_NEWS_LENSES", 10)?,
                category_similarity: parse_f64("BRIEFING_CATEGORY_SIMILARITY", 0.55)?,
                category_cluster_similarity: parse_f64(
                    "BRIEFING_CATEGORY_CLUSTER_SIMILARITY",
                    0.62,
                )?,
                category_absorb_similarity: parse_f64("BRIEFING_CATEGORY_ABSORB_SIMILARITY", 0.45)?,
                centroid_max_weight: parse_i32("BRIEFING_CENTROID_MAX_WEIGHT", 32)?,
                sweep_seconds: parse_i64("BRIEFING_SWEEP_SECONDS", 3_600)?,
                lens_idle_days: parse_i64("BRIEFING_LENS_IDLE_DAYS", 7)?,
            },
            compose_parallelism: parse_usize("BRIEFING_COMPOSE_PARALLELISM", 12)?,
            embedding_batch_size: parse_usize("BRIEFING_CATEGORY_EMBEDDING_BATCH_SIZE", 32)?,
            event_similarity: parse_f64("BRIEFING_NEWS_EVENT_SIMILARITY", 0.78)?,
            max_figures_deep: parse_usize("BRIEFING_MAX_FIGURES_DEEP", 12)?,
            max_compose_attempts: 4,
        };
        config.validate()?;
        Ok(config)
    }

    pub fn validate(&self) -> Result<(), BriefingRefreshWorkerConfigError> {
        self.repository
            .validate()
            .map_err(|_| BriefingRefreshWorkerConfigError::Range("Briefing repository settings"))?;
        if !(1..=16).contains(&self.compose_parallelism) {
            return Err(BriefingRefreshWorkerConfigError::Range(
                "BRIEFING_COMPOSE_PARALLELISM",
            ));
        }
        if !(1..=128).contains(&self.embedding_batch_size) {
            return Err(BriefingRefreshWorkerConfigError::Range(
                "BRIEFING_CATEGORY_EMBEDDING_BATCH_SIZE",
            ));
        }
        if !(0.0..=1.0).contains(&self.event_similarity) || !self.event_similarity.is_finite() {
            return Err(BriefingRefreshWorkerConfigError::Range(
                "BRIEFING_NEWS_EVENT_SIMILARITY",
            ));
        }
        if self.max_figures_deep > 50 {
            return Err(BriefingRefreshWorkerConfigError::Range(
                "BRIEFING_MAX_FIGURES_DEEP",
            ));
        }
        if self.max_compose_attempts == 0 || self.max_compose_attempts > 10 {
            return Err(BriefingRefreshWorkerConfigError::Range(
                "Briefing composition attempts",
            ));
        }
        Ok(())
    }
}

fn parse_usize(
    name: &'static str,
    default: usize,
) -> Result<usize, BriefingRefreshWorkerConfigError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse::<usize>()
            .map_err(|_| BriefingRefreshWorkerConfigError::Invalid { name, value })
    })
}

fn parse_i64(name: &'static str, default: i64) -> Result<i64, BriefingRefreshWorkerConfigError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse::<i64>()
            .map_err(|_| BriefingRefreshWorkerConfigError::Invalid { name, value })
    })
}

fn parse_i32(name: &'static str, default: i32) -> Result<i32, BriefingRefreshWorkerConfigError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse::<i32>()
            .map_err(|_| BriefingRefreshWorkerConfigError::Invalid { name, value })
    })
}

fn parse_f64(name: &'static str, default: f64) -> Result<f64, BriefingRefreshWorkerConfigError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse::<f64>()
            .map_err(|_| BriefingRefreshWorkerConfigError::Invalid { name, value })
    })
}

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum BriefingRefreshWorkerConfigError {
    #[error("{name} has invalid numeric value {value:?}")]
    Invalid { name: &'static str, value: String },
    #[error("{0} is outside its supported range")]
    Range(&'static str),
}

#[cfg(test)]
mod tests {
    use super::*;

    fn valid_config() -> BriefingRefreshWorkerConfig {
        BriefingRefreshWorkerConfig {
            repository: BriefingRefreshConfig {
                masthead_title: DEFAULT_MASTHEAD_TITLE.to_owned(),
                window_min: 3,
                news_window_max: 4,
                new_lens_min_items: 3,
                pending_max_age_seconds: 1_500,
                max_news_lenses: 10,
                category_similarity: 0.55,
                category_cluster_similarity: 0.62,
                category_absorb_similarity: 0.45,
                centroid_max_weight: 32,
                sweep_seconds: 3_600,
                lens_idle_days: 7,
            },
            compose_parallelism: 12,
            embedding_batch_size: 32,
            event_similarity: 0.78,
            max_figures_deep: 12,
            max_compose_attempts: 4,
        }
    }

    #[test]
    fn rejects_unbounded_parallelism_and_similarity() {
        let mut config = valid_config();
        config.compose_parallelism = 0;
        assert!(config.validate().is_err());
        config.compose_parallelism = 1;
        config.event_similarity = f64::NAN;
        assert!(config.validate().is_err());
    }
}
