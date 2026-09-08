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

#[cfg(test)]
mod tests {
    use super::*;

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
