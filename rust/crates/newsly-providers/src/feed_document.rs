//! Preserve direct Media RSS URLs while adapting feed-rs' player precedence.

use quick_xml::events::Event;
use quick_xml::name::ResolveResult;
use quick_xml::{NsReader, Writer};

const MEDIA_RSS_NAMESPACE: &[u8] = b"http://search.yahoo.com/mrss/";

pub(crate) fn preserve_direct_media_urls(bytes: &[u8]) -> Result<Vec<u8>, quick_xml::Error> {
    // feed-rs also accepts JSON Feed; this adapter only handles XML.
    if bytes
        .strip_prefix(b"\xEF\xBB\xBF")
        .unwrap_or(bytes)
        .iter()
        .copied()
        .find(|byte| !byte.is_ascii_whitespace())
        .is_none_or(|byte| byte != b'<')
    {
        return Ok(bytes.to_vec());
    }
    let mut reader = NsReader::from_reader(bytes);
    // Keep feed-rs' accepted text and trailing-markup tolerance while repairing
    // Media RSS player precedence; the canonical parser still validates the feed.
    reader.config_mut().allow_dangling_amp = true;
    reader.config_mut().allow_unmatched_ends = true;
    reader.config_mut().trim_markup_names_in_closing_tags = true;
    let mut writer = Writer::new(Vec::with_capacity(bytes.len()));
    let mut direct_content = Vec::new();
    let mut skip_depth = 0_usize;
    loop {
        let (namespace, event) = reader.read_resolved_event()?;
        let media = matches!(namespace, ResolveResult::Bound(namespace) if namespace.as_ref() == MEDIA_RSS_NAMESPACE);
        match &event {
            Event::Start(element) => {
                if skip_depth > 0 {
                    skip_depth += 1;
                    continue;
                }
                if media
                    && element.local_name().as_ref() == b"player"
                    && direct_content.last() == Some(&true)
                {
                    skip_depth = 1;
                    continue;
                }
                let has_direct_url = media
                    && element.local_name().as_ref() == b"content"
                    && element
                        .attributes()
                        .filter_map(Result::ok)
                        .any(|attribute| {
                            attribute.key.as_ref() == b"url"
                                && !attribute.value.iter().all(u8::is_ascii_whitespace)
                        });
                direct_content.push(has_direct_url);
            }
            Event::Empty(element) => {
                if skip_depth > 0
                    || (media
                        && element.local_name().as_ref() == b"player"
                        && direct_content.last() == Some(&true))
                {
                    continue;
                }
            }
            Event::End(_) => {
                if skip_depth > 0 {
                    skip_depth -= 1;
                    continue;
                }
                direct_content.pop();
            }
            Event::Eof => break,
            _ if skip_depth > 0 => continue,
            _ => {}
        }
        writer.write_event(event)?;
    }
    Ok(writer.into_inner())
}

#[cfg(test)]
mod tests {
    use super::preserve_direct_media_urls;

    #[test]
    fn preserves_direct_url_and_leaves_other_players_unchanged() {
        let xml = br#"<rss xmlns:m="http://search.yahoo.com/mrss/" xmlns:other="urn:other"><channel><item>
          <m:content url="https://cdn.example/audio.mp3" type="audio/mpeg"><m:player url="https://example/player"><name>Player</name></m:player><other:player url="https://example/foreign-nested-player"/></m:content>
          <m:content><m:player url="https://example/player-only"/></m:content>
          <m:content url=""><m:player url="https://example/empty-content"/></m:content>
          <other:content url="https://example/other"><other:player url="https://example/other-player"/></other:content>
          <enclosure url="https://cdn.example/audio.mp3" type="audio/mpeg"/>
        </item></channel></rss>"#;
        let normalized = preserve_direct_media_urls(xml).unwrap();
        let text = String::from_utf8(normalized).unwrap();
        assert!(!text.contains("https://example/player\""));
        for retained in [
            "player-only",
            "empty-content",
            "other-player",
            "foreign-nested-player",
            "<enclosure",
            "https://cdn.example/audio.mp3",
        ] {
            assert!(text.contains(retained), "missing {retained}");
        }
    }

    #[test]
    fn malformed_xml_is_rejected() {
        assert!(preserve_direct_media_urls(b"<rss><channel></rss>").is_err());
    }

    #[test]
    fn accepts_feed_parser_tolerance_without_losing_direct_media() {
        for suffix in ["", "</html>"] {
            let xml = format!(
                r#"<rss version="2.0" xmlns:m="http://search.yahoo.com/mrss/"><channel>
                <title>Arts & Culture</title><link>https://example.com</link><description>A show</description>
                <item><guid>episode</guid><title>Science and Music</title><category>Arts & Culture</category>
                <m:content url="https://cdn.example/audio.mp3" type="audio/mpeg"><m:player url="https://example/player"/></m:content>
                </item></channel></rss>{suffix}"#
            );
            assert_eq!(
                feed_rs::parser::parse(xml.as_bytes())
                    .unwrap()
                    .entries
                    .len(),
                1
            );
            let normalized = preserve_direct_media_urls(xml.as_bytes()).unwrap();
            let parsed = feed_rs::parser::parse(normalized.as_slice()).unwrap();
            assert_eq!(
                parsed.entries[0].title.as_ref().unwrap().content,
                "Science and Music"
            );
            assert_eq!(
                crate::feed_validation::entry_audio_url(&parsed.entries[0]).as_deref(),
                Some("https://cdn.example/audio.mp3")
            );
        }
    }

    #[test]
    fn preserves_atom_relative_urls_and_json_feed_bytes() {
        let xml = br#"<feed xmlns="http://www.w3.org/2005/Atom" xmlns:m="http://search.yahoo.com/mrss/" xml:base="https://cdn.example/">
          <title>Show</title><entry><id>one</id><title>One</title><m:content url="audio.mp3" type="audio/mpeg"><m:player url="player"/></m:content></entry>
        </feed>"#;
        let normalized = preserve_direct_media_urls(xml).unwrap();
        let feed = feed_rs::parser::parse(normalized.as_slice()).unwrap();
        assert_eq!(
            crate::feed_validation::entry_audio_url(&feed.entries[0]).as_deref(),
            Some("https://cdn.example/audio.mp3")
        );
        let json = br#"{"version":"https://jsonfeed.org/version/1.1","title":"Show","items":[]}"#;
        assert_eq!(preserve_direct_media_urls(json).unwrap(), json);
    }
}
