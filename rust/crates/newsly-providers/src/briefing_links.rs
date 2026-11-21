//! The single Markdown interpretation used by composition validation and publication.
use std::ops::Range;

use pulldown_cmark::{Event, LinkType, Parser, Tag, TagEnd};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingLink {
    pub range: Range<usize>,
    pub label: String,
    pub uri: String,
    pub source_key: String,
}

/// Only actual inline Markdown links count, never bare URIs, images, or code.
/// Unknown identities remain present so the caller can reject them against its allowlist.
pub fn briefing_links(markdown: &str) -> Vec<BriefingLink> {
    let mut links = Vec::new();
    let mut current: Option<BriefingLink> = None;
    for (event, range) in Parser::new(markdown).into_offset_iter() {
        match event {
            Event::Start(Tag::Link {
                link_type: LinkType::Inline,
                dest_url,
                ..
            }) => {
                if let Some(path) = dest_url.strip_prefix("newsly://briefing/") {
                    current = Some(BriefingLink {
                        range,
                        label: String::new(),
                        uri: dest_url.to_string(),
                        source_key: path.replace('/', ":"),
                    });
                }
            }
            Event::Text(text) | Event::Code(text) => {
                if let Some(link) = &mut current {
                    link.label.push_str(&text);
                }
            }
            Event::SoftBreak | Event::HardBreak => {
                if let Some(link) = &mut current {
                    link.label.push(' ');
                }
            }
            Event::End(TagEnd::Link) => {
                if let Some(mut link) = current.take() {
                    link.range.end = range.end;
                    if markdown[..link.range.start].ends_with("**")
                        && markdown[link.range.end..].starts_with("**")
                    {
                        link.range.start -= 2;
                        link.range.end += 2;
                    }
                    links.push(link);
                }
            }
            _ => {}
        }
    }
    links
}

/// Splits one paragraph at `.`, `!`, or `?` followed by whitespace or the end, ignoring
/// terminators inside links. Composition validation and publication share this split.
pub fn briefing_sentences(paragraph: &str) -> Vec<&str> {
    let link_ranges = briefing_links(paragraph)
        .into_iter()
        .map(|link| link.range)
        .collect::<Vec<_>>();
    let mut sentences = Vec::new();
    let mut start = 0_usize;
    for (index, character) in paragraph.char_indices() {
        if !matches!(character, '.' | '!' | '?')
            || link_ranges.iter().any(|range| range.contains(&index))
        {
            continue;
        }
        let after = index + character.len_utf8();
        if paragraph[after..]
            .chars()
            .next()
            .is_none_or(char::is_whitespace)
        {
            let sentence = paragraph[start..after].trim();
            if !sentence.is_empty() {
                sentences.push(sentence);
            }
            start = after;
        }
    }
    let tail = paragraph[start..].trim();
    if !tail.is_empty() {
        sentences.push(tail);
    }
    sentences
}

/// The spoken and counted text: link labels without targets, no bold markers, single spaces.
pub fn briefing_plain_text(markdown: &str) -> String {
    let mut without_links = String::new();
    let mut cursor = 0;
    for link in briefing_links(markdown) {
        without_links.push_str(&markdown[cursor..link.range.start]);
        without_links.push_str(&link.label);
        cursor = link.range.end;
    }
    without_links.push_str(&markdown[cursor..]);
    without_links
        .replace("**", "")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sentences_ignore_terminators_inside_links_and_keep_unterminated_tails() {
        let paragraph = "[A study v2.0 asks why?](newsly://briefing/content/1) It finds 3.5% gains. \
                         Critics disagree! Is it real? Maybe not";
        assert_eq!(
            briefing_sentences(paragraph),
            [
                "[A study v2.0 asks why?](newsly://briefing/content/1) It finds 3.5% gains.",
                "Critics disagree!",
                "Is it real?",
                "Maybe not",
            ]
        );
        assert!(briefing_sentences("   ").is_empty());
    }

    #[test]
    fn plain_text_keeps_link_labels_and_drops_targets_and_bold() {
        assert_eq!(
            briefing_plain_text("**Lead** [Source  one](newsly://briefing/content/1)\n\nends."),
            "Lead Source one ends."
        );
    }

    #[test]
    fn citation_corpus_preserves_labels_and_exact_identity() {
        for (label, expected) in [
            ("Plain", "Plain"),
            ("A [study]", "A [study]"),
            (r"A \[study\]", "A [study]"),
            ("**你好** (update)!", "你好 (update)!"),
        ] {
            let markdown = format!("Before [{label}](newsly://briefing/content/10) after.");
            let links = briefing_links(&markdown);
            assert_eq!(links.len(), 1, "{markdown}");
            assert_eq!(links[0].label, expected);
            assert_eq!(links[0].source_key, "content:10");
            assert_eq!(
                &markdown[links[0].range.clone()],
                format!("[{label}](newsly://briefing/content/10)")
            );
        }
    }

    #[test]
    fn citation_corpus_does_not_count_non_links() {
        for markdown in [
            "newsly://briefing/content/1",
            "`[A](newsly://briefing/content/1)`",
            "```\n[A](newsly://briefing/content/1)\n```",
            "![A](newsly://briefing/content/1)",
            "[broken](newsly://briefing/content/1",
            "<newsly://briefing/content/1>",
        ] {
            assert!(briefing_links(markdown).is_empty(), "{markdown}");
        }
    }
}
