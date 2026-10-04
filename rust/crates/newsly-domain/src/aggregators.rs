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

    pub const fn site_url(self) -> &'static str {
        match self {
            Self::HackerNews => "https://news.ycombinator.com",
            Self::Techmeme => "https://www.techmeme.com",
            Self::Mediagazer => "https://mediagazer.com",
            Self::Memeorandum => "https://www.memeorandum.com",
            Self::SciUrls => "https://sciurls.com",
            Self::FinUrls => "https://finurls.com",
            Self::Brutalist => "https://brutalist.report",
            Self::Arxiv => "https://arxiv.org",
            Self::HfPapers => "https://huggingface.co/papers",
        }
    }

    pub const fn description(self) -> &'static str {
        match self {
            Self::HackerNews => "Tech, startups and engineering discussion",
            Self::Techmeme => "Top tech industry headlines, clustered",
            Self::Mediagazer => "Media industry news and coverage",
            Self::Memeorandum => "US politics, policy and punditry",
            Self::SciUrls => "Science news across disciplines",
            Self::FinUrls => "Finance, markets and economics",
            Self::Brutalist => "Headlines by topic: science, business, politics, sports",
            Self::Arxiv => "New AI and machine learning research papers, by arXiv category",
            Self::HfPapers => "Trending AI research papers, upvoted by the Hugging Face community",
        }
    }

    /// Lowercase phrases a source-discovery query may use for this aggregator, besides its name.
    const fn search_terms(self) -> &'static [&'static str] {
        match self {
            Self::HackerNews => &[
                "hn",
                "y combinator",
                "startup",
                "startups",
                "programming",
                "software engineering",
                "developer",
                "developers",
                "tech news",
            ],
            Self::Techmeme => &[
                "tech news",
                "technology news",
                "tech industry",
                "silicon valley",
            ],
            Self::Mediagazer => &["media news", "media industry", "journalism", "publishing"],
            Self::Memeorandum => &["politics", "political news", "us politics", "policy"],
            Self::SciUrls => &["science", "science news", "scientific"],
            Self::FinUrls => &[
                "finance",
                "financial news",
                "markets",
                "stock market",
                "economics",
                "economy",
                "investing",
            ],
            Self::Brutalist => &["headlines", "science", "business", "politics", "sports"],
            Self::Arxiv => &[
                "paper",
                "papers",
                "preprint",
                "preprints",
                "research",
                "ai research",
                "ml research",
                "machine learning",
                "artificial intelligence",
                "nlp",
                "computer vision",
                "language models",
                "statistics",
            ],
            Self::HfPapers => &[
                "hugging face",
                "huggingface",
                "hf",
                "paper",
                "papers",
                "research",
                "ai research",
                "ml research",
                "machine learning",
                "artificial intelligence",
                "llm",
                "llms",
                "language models",
            ],
        }
    }

    /// Catalog aggregators relevant to a free-text source-discovery query, best match first.
    /// Matching is on whole words; a query naming aggregators generically returns the full
    /// catalog when nothing more specific matches.
    pub fn matching_query(query: &str) -> Vec<Self> {
        let query = format!(" {} ", normalize_words(query));
        let mut scored = Self::ALL
            .into_iter()
            .filter_map(|key| {
                let named = [key.as_str(), key.display_name()]
                    .into_iter()
                    .any(|name| contains_phrase(&query, name));
                let terms = key
                    .search_terms()
                    .iter()
                    .chain(key.topics())
                    .filter(|term| contains_phrase(&query, term))
                    .count();
                let score = usize::from(named) * 100 + terms;
                (score > 0).then_some((score, key))
            })
            .collect::<Vec<_>>();
        if scored.is_empty()
            && [" aggregator ", " aggregators "]
                .iter()
                .any(|word| query.contains(word))
        {
            return Self::ALL.to_vec();
        }
        scored.sort_by(|left, right| right.0.cmp(&left.0));
        scored.into_iter().map(|(_, key)| key).collect()
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

/// Lowercases and replaces everything except letters, digits and dots with single spaces, so
/// phrases match on word boundaries ("hn" never matches inside "john").
fn normalize_words(value: &str) -> String {
    value
        .split(|character: char| !(character.is_alphanumeric() || character == '.'))
        .map(|word| word.trim_matches('.'))
        .filter(|word| !word.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase()
}

fn contains_phrase(padded_query: &str, phrase: &str) -> bool {
    let phrase = normalize_words(phrase);
    !phrase.is_empty() && padded_query.contains(&format!(" {phrase} "))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matching_query_finds_paper_aggregators_for_research_queries() {
        let matches = AggregatorKey::matching_query("Find me sources for new AI research papers");
        assert_eq!(
            &matches[..2],
            &[AggregatorKey::Arxiv, AggregatorKey::HfPapers]
        );
        assert_eq!(
            AggregatorKey::matching_query("can I follow Hugging Face daily papers?")[0],
            AggregatorKey::HfPapers
        );
        assert_eq!(
            AggregatorKey::matching_query("subscribe to arXiv cs.CL")[0],
            AggregatorKey::Arxiv
        );
    }

    #[test]
    fn matching_query_uses_whole_words_and_generic_aggregator_requests() {
        assert!(AggregatorKey::matching_query("podcasts like John Oliver").is_empty());
        assert_eq!(
            AggregatorKey::matching_query("what news aggregators can I add?"),
            AggregatorKey::ALL.to_vec()
        );
        assert_eq!(
            AggregatorKey::matching_query("hacker news style aggregators")[0],
            AggregatorKey::HackerNews
        );
    }

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
