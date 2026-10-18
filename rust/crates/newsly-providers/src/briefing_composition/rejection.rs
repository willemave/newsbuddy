use thiserror::Error;

/// Provider-owned rejection categories. Details remain available for corrective feedback;
/// telemetry must use only `code()`.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum BriefingLayoutRejection {
    #[error("provider returned no structured output")]
    MissingStructuredOutput,
    #[error("{0}")]
    Decode(String),
    #[error("layout has no blocks")]
    EmptyLayout,
    #[error("suggested quotes must have unique bounded IDs and text")]
    InvalidSuggestedQuotes,
    #[error("passage markdown is empty")]
    EmptyPassage,
    #[error("figure does not reference an imaged source")]
    InvalidFigure,
    #[error("pullquote references an unknown or reused suggestion")]
    InvalidPullquote,
    #[error("layout contains an unknown Briefing source link")]
    UnknownSourceLink,
    #[error("source {0} must be linked at least once")]
    SourceLinkMissing(String),
    #[error("source {0} must be linked exactly once")]
    NewsSourceLinkMissing(String),
    #[error("source {0} must be linked exactly once")]
    SourceLinkDuplicated(String),
    #[error("news layout must contain exactly one passage")]
    NewsPassageCount,
    #[error("news layout must contain exactly one paragraph")]
    NewsParagraphCount,
}

impl BriefingLayoutRejection {
    pub const fn code(&self) -> &'static str {
        match self {
            Self::MissingStructuredOutput => "missing_structured_output",
            Self::Decode(_) => "decode_failed",
            Self::EmptyLayout => "empty_layout",
            Self::InvalidSuggestedQuotes => "invalid_suggested_quotes",
            Self::EmptyPassage => "empty_passage",
            Self::InvalidFigure => "invalid_figure",
            Self::InvalidPullquote => "invalid_pullquote",
            Self::UnknownSourceLink => "unknown_source_link",
            Self::SourceLinkMissing(_) | Self::NewsSourceLinkMissing(_) => "source_link_missing",
            Self::SourceLinkDuplicated(_) => "source_link_duplicated",
            Self::NewsPassageCount => "news_passage_count",
            Self::NewsParagraphCount => "news_paragraph_count",
        }
    }
}
