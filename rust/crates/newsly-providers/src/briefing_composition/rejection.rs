use thiserror::Error;

/// Provider-owned rejection categories. Details remain available for corrective feedback;
/// telemetry must use only `code()`.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum BriefingLayoutRejection {
    #[error("provider returned no structured output")]
    MissingStructuredOutput,
    #[error("{0}")]
    Decode(String),
    #[error("passage markdown is empty")]
    EmptyPassage,
    #[error("layout contains an unknown Briefing source link")]
    UnknownSourceLink,
    #[error("source {0} must be linked at least once")]
    SourceLinkMissing(String),
    #[error("source {0} must be linked exactly once")]
    NewsSourceLinkMissing(String),
    #[error("source {0} must be linked exactly once")]
    SourceLinkDuplicated(String),
    #[error("news layout must contain exactly one paragraph")]
    NewsParagraphCount,
    #[error("card summary has {0} sentences; rewrite it in at most six sentences")]
    CardSentenceCount(usize),
    #[error("card summary has {0} words; rewrite it in at most 150 words, targeting 60-100 words")]
    CardWordCount(usize),
}

impl BriefingLayoutRejection {
    pub const fn code(&self) -> &'static str {
        match self {
            Self::MissingStructuredOutput => "missing_structured_output",
            Self::Decode(_) => "decode_failed",
            Self::EmptyPassage => "empty_passage",
            Self::UnknownSourceLink => "unknown_source_link",
            Self::SourceLinkMissing(_) | Self::NewsSourceLinkMissing(_) => "source_link_missing",
            Self::SourceLinkDuplicated(_) => "source_link_duplicated",
            Self::NewsParagraphCount => "news_paragraph_count",
            Self::CardSentenceCount(_) => "card_sentence_count",
            Self::CardWordCount(_) => "card_word_count",
        }
    }
}
