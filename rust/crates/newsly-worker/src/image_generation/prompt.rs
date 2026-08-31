use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

const INFOGRAPHIC_PROMPT_PREFIX: &str = r"Create a premium no-text editorial illustration for Newsly.

Hard constraints:
- No readable text, letters, numbers, labels, captions, logos, or watermarks
- No poster layout, newspaper layout, document pages, magazine spreads, screenshots, dashboards, or UI chrome
- 16:9 aspect ratio optimized for mobile display
- One dominant visual metaphor or one coherent scene, never a collage
- One focal subject with strong negative space and clear foreground/background separation
- Bold, graphic, and immediately legible at thumbnail size
- Premium magazine image with tactile, materially believable surfaces
- Purposeful asymmetry, decisive frame fill, and clean negative space
- One surprising material or object derived directly from the story topic
- Refined topic-derived palette with 2 to 4 dominant colors; avoid default purple/cyan tech color schemes
- Avoid generic AI robots, glowing blue circuitry, corporate clip art, and familiar stock metaphors
- If the story centers on a named real person, do not invent or approximate their recognizable face; create a non-literal portrait through their craft, tools, materials, silhouette, or environment

Visual brief:";

const INFOGRAPHIC_PROMPT_SUFFIX: &str = r"

Output goal:
Create a distinctive editorial image that communicates the story instantly without rendering any words.";

const TECH_KEYWORDS: [&str; 17] = [
    "ai",
    "artificial intelligence",
    "software",
    "automation",
    "agent",
    "agents",
    "tool",
    "tools",
    "mcp",
    "token",
    "tokens",
    "notion",
    "future",
    "workflow",
    "system",
    "factory",
    "compute",
];

pub(super) fn build_infographic_prompt(
    content_type: &str,
    title: Option<&str>,
    metadata: &Value,
) -> Option<String> {
    let runtime = runtime_metadata_view(metadata);
    let summary = runtime.get("summary")?.as_object()?;
    let display_title = resolve_display_title(title, summary);
    let (overview, mut key_points) = summary_context(summary);
    if key_points.is_empty() && !overview.is_empty() {
        key_points.push(overview.clone());
    }
    let clues = {
        let mut values = key_points
            .iter()
            .take(3)
            .filter(|value| !value.is_empty())
            .cloned()
            .collect::<Vec<_>>();
        if !overview.is_empty() {
            values.insert(0, overview.clone());
        }
        clamp_text(
            &if values.is_empty() {
                display_title.clone()
            } else {
                values.join(" ; ")
            },
            280,
        )
    };
    let subject = clamp_text(
        key_points
            .first()
            .map(String::as_str)
            .filter(|value| !value.is_empty())
            .or_else(|| (!overview.is_empty()).then_some(overview.as_str()))
            .unwrap_or(&display_title),
        120,
    );
    let topic_text = format!("{display_title} {overview} {}", key_points.join(" "));
    let (metaphor, scene_direction) = if is_tech_story(&topic_text) {
        (
            "a tangible system of signals, tools, and pressure rather than a literal UI",
            "an editorial still life or physical scene that implies software, networks, or automation without screens",
        )
    } else {
        (
            "a single symbolic scene that turns the story theme into a physical moment",
            "a calm but high-contrast editorial composition with one hero subject and a few supporting elements",
        )
    };
    Some(format!(
        "{INFOGRAPHIC_PROMPT_PREFIX}\n- Story context: {clues}\n- Primary subject: {subject}\n- Visual metaphor: {metaphor}\n- Scene direction: {scene_direction}\n- Supporting cues: {clues}{INFOGRAPHIC_PROMPT_SUFFIX}"
    ))
    .filter(|_| matches!(content_type, "article" | "podcast" | "unknown" | "insight_report"))
}

pub(super) fn image_input_fingerprint(prompt: &str) -> String {
    hex_encode(&Sha256::digest(prompt.as_bytes()))
}

pub(super) fn has_generated_image(metadata: &Value) -> bool {
    runtime_metadata_view(metadata)
        .get("image_generated_at")
        .is_some_and(json_truthy)
}

pub(super) fn runtime_metadata_view(metadata: &Value) -> Map<String, Value> {
    let Some(root) = metadata.as_object() else {
        return Map::new();
    };
    let mut domain = root
        .get("domain")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    for (key, value) in root {
        if !matches!(key.as_str(), "domain" | "processing") {
            domain.entry(key.clone()).or_insert_with(|| value.clone());
        }
    }
    if let Some(processing) = root.get("processing").and_then(Value::as_object) {
        for (key, value) in processing {
            domain.insert(key.clone(), value.clone());
        }
    }
    domain
}

fn summary_context(summary: &Map<String, Value>) -> (String, Vec<String>) {
    if let Some(payload) = summary
        .get("artifact")
        .and_then(Value::as_object)
        .and_then(|artifact| artifact.get("payload"))
        .and_then(Value::as_object)
    {
        let overview = first_clean_text(
            [
                summary.get("one_line"),
                payload.get("overview"),
                payload.get("takeaway"),
            ]
            .into_iter(),
        )
        .map_or_else(String::new, |value| clamp_text(&value, 240));
        let points = payload
            .get("key_points")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .take(4)
            .filter_map(|item| {
                let object = item.as_object()?;
                let joined = [object.get("heading"), object.get("content")]
                    .into_iter()
                    .flatten()
                    .filter_map(value_as_clean_string)
                    .collect::<Vec<_>>()
                    .join(" ");
                (!joined.is_empty()).then(|| clamp_text(&joined, 220))
            })
            .collect();
        return (overview, points);
    }
    let overview = first_clean_text(
        [
            summary.get("summary"),
            summary.get("overview"),
            summary.get("hook"),
            summary.get("takeaway"),
        ]
        .into_iter(),
    )
    .map_or_else(String::new, |value| clamp_text(&value, 240));
    let points = summary
        .get("key_points")
        .or_else(|| summary.get("bullet_points"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .take(4)
        .filter_map(|item| match item {
            Value::Object(object) => first_clean_text(
                [
                    object.get("text"),
                    object.get("point"),
                    object.get("insight"),
                ]
                .into_iter(),
            ),
            Value::String(value) => clean_text(value),
            _ => None,
        })
        .map(|value| clamp_text(&value, 220))
        .collect();
    (overview, points)
}

fn resolve_display_title(title: Option<&str>, summary: &Map<String, Value>) -> String {
    let display_title = summary
        .get("title")
        .and_then(value_as_clean_string)
        .or_else(|| {
            summary
                .get("feed_preview")
                .and_then(Value::as_object)
                .and_then(|preview| preview.get("title"))
                .and_then(value_as_clean_string)
        })
        .or_else(|| title.and_then(clean_text))
        .unwrap_or_else(|| "Untitled".to_owned());
    clamp_text(&display_title, 180)
}

fn first_clean_text<'a>(values: impl Iterator<Item = Option<&'a Value>>) -> Option<String> {
    values.flatten().find_map(value_as_clean_string)
}

fn value_as_clean_string(value: &Value) -> Option<String> {
    value.as_str().and_then(clean_text)
}

fn clean_text(value: &str) -> Option<String> {
    let normalized = value.split_whitespace().collect::<Vec<_>>().join(" ");
    (!normalized.is_empty()).then_some(normalized)
}

fn clamp_text(value: &str, max_chars: usize) -> String {
    let normalized = value.split_whitespace().collect::<Vec<_>>().join(" ");
    if normalized.chars().count() <= max_chars {
        return normalized;
    }
    let mut truncated = normalized
        .chars()
        .take(max_chars.saturating_sub(1))
        .collect::<String>();
    while truncated
        .chars()
        .last()
        .is_some_and(|character| " ,;:-".contains(character))
    {
        truncated.pop();
    }
    truncated.push('…');
    truncated
}

fn is_tech_story(value: &str) -> bool {
    let normalized = value.to_ascii_lowercase();
    TECH_KEYWORDS
        .iter()
        .any(|keyword| normalized.contains(keyword))
}

fn json_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::String(value) => !value.trim().is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
        Value::Number(_) => true,
    }
}

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        encoded.push(char::from(HEX[usize::from(byte >> 4)]));
        encoded.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    encoded
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn builds_artifact_prompt_from_current_longform_contract() {
        let metadata = json!({
            "domain": {
                "summary": {
                    "title": "The Factory of Agents",
                    "one_line": "Teams are replacing handoffs with coordinated software agents.",
                    "artifact": {
                        "payload": {
                            "key_points": [
                                {"heading": "Coordination", "content": "Shared state becomes the bottleneck."},
                                {"heading": "Control", "content": "Boundaries matter more than autonomy."}
                            ]
                        }
                    }
                }
            }
        });
        let prompt = build_infographic_prompt("article", None, &metadata).unwrap();
        assert!(prompt.contains("Coordination Shared state becomes the bottleneck."));
        assert!(prompt.contains("without screens"));
        assert!(prompt.contains("No readable text"));
    }

    #[test]
    fn fingerprint_changes_when_prompt_owned_summary_changes() {
        let first = json!({"summary": {"title": "One", "overview": "Summary"}});
        let second = json!({"summary": {"title": "One", "overview": "Changed summary"}});
        let first = build_infographic_prompt("article", None, &first).unwrap();
        let second = build_infographic_prompt("article", None, &second).unwrap();
        assert_ne!(
            image_input_fingerprint(&first),
            image_input_fingerprint(&second)
        );
    }

    #[test]
    fn processing_metadata_wins_over_legacy_root_values() {
        let metadata = json!({
            "image_generated_at": null,
            "processing": {"image_generated_at": "2026-08-30T00:00:00Z"}
        });
        assert!(has_generated_image(&metadata));
    }
}
