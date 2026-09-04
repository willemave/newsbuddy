use quick_xml::Reader;
use quick_xml::events::Event;
use quick_xml::name::QName;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(super) struct PodcastEntryMetadata {
    pub(super) episode_number: Option<u64>,
    pub(super) duration_seconds: Option<u64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Field {
    Episode,
    Duration,
}

pub(super) fn podcast_entry_metadata(bytes: &[u8]) -> Vec<PodcastEntryMetadata> {
    let mut reader = Reader::from_reader(bytes);
    reader.config_mut().trim_text(true);
    let mut buffer = Vec::new();
    let mut entries = Vec::new();
    let mut current = None;
    let mut field = None;
    let mut value = String::new();
    loop {
        match reader.read_event_into(&mut buffer) {
            Ok(Event::Start(element)) if local_name_is(element.name(), b"item") => {
                current = Some(PodcastEntryMetadata::default());
            }
            Ok(Event::Start(element)) if current.is_some() => {
                field = field_for_name(element.name());
                value.clear();
            }
            Ok(Event::Text(text)) if field.is_some() => {
                if let Ok(text) = text.decode() {
                    value.push_str(&text);
                }
            }
            Ok(Event::CData(text)) if field.is_some() => {
                if let Ok(text) = text.decode() {
                    value.push_str(&text);
                }
            }
            Ok(Event::End(element)) if local_name_is(element.name(), b"item") => {
                if let Some(entry) = current.take() {
                    entries.push(entry);
                }
                field = None;
                value.clear();
            }
            Ok(Event::End(element))
                if field.is_some() && field_for_name(element.name()) == field =>
            {
                if let (Some(entry), Some(field)) = (current.as_mut(), field.take()) {
                    apply_value(entry, field, &value);
                }
                value.clear();
            }
            Ok(Event::Eof) | Err(_) => break,
            Ok(_) => {}
        }
        buffer.clear();
    }
    entries
}

fn field_for_name(name: QName<'_>) -> Option<Field> {
    if local_name_is(name, b"episode") {
        Some(Field::Episode)
    } else if local_name_is(name, b"duration") {
        Some(Field::Duration)
    } else {
        None
    }
}

fn local_name_is(name: QName<'_>, expected: &[u8]) -> bool {
    name.local_name().as_ref().eq_ignore_ascii_case(expected)
}

fn apply_value(entry: &mut PodcastEntryMetadata, field: Field, value: &str) {
    match field {
        Field::Episode => entry.episode_number = value.trim().parse().ok(),
        Field::Duration => entry.duration_seconds = parse_duration(value),
    }
}

fn parse_duration(value: &str) -> Option<u64> {
    let parts = value
        .trim()
        .split(':')
        .map(str::parse::<u64>)
        .collect::<Result<Vec<_>, _>>()
        .ok()?;
    match parts.as_slice() {
        [seconds] => Some(*seconds),
        [minutes, seconds] => minutes.checked_mul(60)?.checked_add(*seconds),
        [hours, minutes, seconds] => hours
            .checked_mul(3_600)?
            .checked_add(minutes.checked_mul(60)?)?
            .checked_add(*seconds),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::{PodcastEntryMetadata, parse_duration, podcast_entry_metadata};

    #[test]
    fn parses_legacy_itunes_episode_metadata() {
        let entries = podcast_entry_metadata(
            br#"<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
                <item><itunes:episode>2</itunes:episode><itunes:duration>30:45</itunes:duration></item>
                <item><itunes:duration><![CDATA[1:15:30]]></itunes:duration></item>
            </channel></rss>"#,
        );
        assert_eq!(
            entries,
            [
                PodcastEntryMetadata {
                    episode_number: Some(2),
                    duration_seconds: Some(1_845),
                },
                PodcastEntryMetadata {
                    episode_number: None,
                    duration_seconds: Some(4_530),
                },
            ]
        );
    }

    #[test]
    fn duration_parser_is_checked_and_rejects_unknown_shapes() {
        assert_eq!(parse_duration("180"), Some(180));
        assert_eq!(parse_duration("1:23:45"), Some(5_025));
        assert_eq!(parse_duration("invalid"), None);
        assert_eq!(parse_duration("1:2:3:4"), None);
        assert_eq!(parse_duration("18446744073709551615:1"), None);
    }
}
