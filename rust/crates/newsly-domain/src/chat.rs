/// Historical Deep Research sessions remain readable, but cannot accept new work.
pub fn is_retired_deep_research(
    provider: Option<&str>,
    session_type: Option<&str>,
    model: Option<&str>,
) -> bool {
    [provider, session_type]
        .into_iter()
        .flatten()
        .any(|value| value.trim().eq_ignore_ascii_case("deep_research"))
        || model.is_some_and(|value| {
            let normalized = value.trim().to_ascii_lowercase();
            normalized.starts_with("deep_research:") || normalized.contains("deep-research")
        })
}

#[cfg(test)]
mod tests {
    use super::is_retired_deep_research;

    #[test]
    fn historical_research_markers_are_case_and_whitespace_insensitive() {
        for (provider, session_type, model) in [
            (Some(" Deep_Research\n"), None, None),
            (None, Some(" DEEP_RESEARCH "), None),
            (
                Some("openai"),
                Some("knowledge_chat"),
                Some("openai:o4-mini-deep-research"),
            ),
            (
                None,
                None,
                Some(" OPENAI:O4-MINI-DEEP-RESEARCH-2025-06-26 "),
            ),
            (None, None, Some(" DEEP_RESEARCH:o4-mini ")),
        ] {
            assert!(is_retired_deep_research(provider, session_type, model));
        }
    }

    #[test]
    fn regular_chat_models_remain_writable() {
        assert!(!is_retired_deep_research(None, None, None));
        assert!(!is_retired_deep_research(
            Some(" OpenAI "),
            Some("knowledge_chat"),
            Some("openai:gpt-6.1-sol"),
        ));
    }
}
