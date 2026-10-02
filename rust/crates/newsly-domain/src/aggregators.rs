//! Canonical catalog of the shared global aggregators.
//!
//! Every crate and every SQL visibility predicate reads keys and topic support from here, so a
//! new aggregator is one catalog entry rather than a hunt through hardcoded lists.

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum AggregatorKey {
    HackerNews,
    Techmeme,
    Mediagazer,
    Memeorandum,
    SciUrls,
    FinUrls,
    Brutalist,
    Arxiv,
    HfPapers,
}

/// arXiv categories fetched globally; users narrow them with aggregator `topics`.
const ARXIV_CATEGORIES: [&str; 5] = ["cs.AI", "cs.LG", "cs.CL", "cs.CV", "stat.ML"];

/// Brutalist Report topic pages fetched globally; users narrow them with aggregator `topics`.
const BRUTALIST_TOPICS: [&str; 4] = ["science", "business", "politics", "sports"];

/// Lowercase wire keys of every supported aggregator, in catalog order.
pub const AGGREGATOR_KEY_NAMES: [&str; AggregatorKey::ALL.len()] = {
    let mut names = [""; AggregatorKey::ALL.len()];
    let mut index = 0;
    while index < names.len() {
        names[index] = AggregatorKey::ALL[index].as_str();
        index += 1;
    }
    names
};

impl AggregatorKey {
    pub const ALL: [Self; 9] = [
        Self::HackerNews,
        Self::Techmeme,
        Self::Mediagazer,
        Self::Memeorandum,
        Self::SciUrls,
        Self::FinUrls,
        Self::Brutalist,
        Self::Arxiv,
        Self::HfPapers,
    ];

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::HackerNews => "hackernews",
            Self::Techmeme => "techmeme",
            Self::Mediagazer => "mediagazer",
            Self::Memeorandum => "memeorandum",
            Self::SciUrls => "sciurls",
            Self::FinUrls => "finurls",
            Self::Brutalist => "brutalist",
            Self::Arxiv => "arxiv",
            Self::HfPapers => "hfpapers",
        }
    }

    pub const fn display_name(self) -> &'static str {
        match self {
            Self::HackerNews => "Hacker News",
            Self::Techmeme => "Techmeme",
            Self::Mediagazer => "Mediagazer",
            Self::Memeorandum => "Memeorandum",
            Self::SciUrls => "SciURLs",
            Self::FinUrls => "FinURLs",
            Self::Brutalist => "Brutalist Report",
            Self::Arxiv => "arXiv",
            Self::HfPapers => "Hugging Face Papers",
        }
    }

    /// Topics a subscriber may select; empty when the aggregator has no topic filter.
    pub const fn topics(self) -> &'static [&'static str] {
        match self {
            Self::Brutalist => &BRUTALIST_TOPICS,
            Self::Arxiv => &ARXIV_CATEGORIES,
            _ => &[],
        }
    }

    /// Returns the catalog spelling of an offered topic, matched case-insensitively. Subscriber
    /// configs store this spelling so visibility can match item topics exactly.
    pub fn canonical_topic(self, topic: &str) -> Option<&'static str> {
        let topic = topic.trim();
        self.topics()
            .iter()
            .copied()
            .find(|candidate| candidate.eq_ignore_ascii_case(topic))
    }

    /// Resolves an exact lowercase wire key; unlike [`Self::parse`] it accepts no aliases.
    pub fn from_key(key: &str) -> Option<Self> {
        Self::ALL
            .into_iter()
            .find(|candidate| candidate.as_str() == key)
    }

    pub fn parse(value: &str) -> Option<Self> {
        let normalized = value
            .chars()
            .filter(char::is_ascii_alphanumeric)
            .flat_map(char::to_lowercase)
            .collect::<String>();
        match normalized.as_str() {
            "hackernews" | "hn" => Some(Self::HackerNews),
            "techmeme" => Some(Self::Techmeme),
            "mediagazer" => Some(Self::Mediagazer),
            "memeorandum" => Some(Self::Memeorandum),
            "sciurls" => Some(Self::SciUrls),
            "finurls" => Some(Self::FinUrls),
            "brutalist" | "brutalistreport" => Some(Self::Brutalist),
            "arxiv" => Some(Self::Arxiv),
            "hfpapers" | "huggingfacepapers" | "hfdailypapers" => Some(Self::HfPapers),
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn key_names_follow_catalog_and_round_trip() {
        for (key, name) in AggregatorKey::ALL.into_iter().zip(AGGREGATOR_KEY_NAMES) {
            assert_eq!(key.as_str(), name);
            assert_eq!(AggregatorKey::parse(name), Some(key));
        }
    }

    #[test]
    fn canonical_topic_is_case_insensitive_and_scoped() {
        assert_eq!(
            AggregatorKey::Arxiv.canonical_topic(" CS.cl "),
            Some("cs.CL")
        );
        assert_eq!(AggregatorKey::Arxiv.canonical_topic("science"), None);
        assert_eq!(AggregatorKey::HackerNews.canonical_topic("cs.CL"), None);
    }

    #[test]
    fn from_key_accepts_only_exact_wire_keys() {
        assert_eq!(
            AggregatorKey::from_key("hfpapers"),
            Some(AggregatorKey::HfPapers)
        );
        assert_eq!(AggregatorKey::from_key("hn"), None);
        assert_eq!(AggregatorKey::from_key("ArXiv"), None);
    }
}
