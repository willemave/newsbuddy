use serde_json::Value;

/// Mirrors Python's compact, sorted `json.dumps` representation.
///
/// The retired runtime used the default `ensure_ascii=True` output in durable dedupe and
/// idempotency keys. Keep that exact byte representation while those keys can coexist with Rust.
pub fn python_canonical_json(value: &Value) -> String {
    let mut output = String::new();
    write_value(value, &mut output);
    output
}

fn write_value(value: &Value, output: &mut String) {
    match value {
        Value::Null => output.push_str("null"),
        Value::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
        Value::Number(value) => output.push_str(&value.to_string()),
        Value::String(value) => write_string(value, output),
        Value::Array(values) => {
            output.push('[');
            for (index, value) in values.iter().enumerate() {
                if index != 0 {
                    output.push(',');
                }
                write_value(value, output);
            }
            output.push(']');
        }
        Value::Object(values) => {
            output.push('{');
            let mut fields = values.iter().collect::<Vec<_>>();
            fields.sort_by(|(left, _), (right, _)| left.cmp(right));
            for (index, (key, value)) in fields.into_iter().enumerate() {
                if index != 0 {
                    output.push(',');
                }
                write_string(key, output);
                output.push(':');
                write_value(value, output);
            }
            output.push('}');
        }
    }
}

fn write_string(value: &str, output: &mut String) {
    output.push('"');
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\u{0008}' => output.push_str("\\b"),
            '\u{000c}' => output.push_str("\\f"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            character if character <= '\u{001f}' => {
                push_unicode_escape(u32::from(character), output);
            }
            character if character.is_ascii() => output.push(character),
            character => {
                let codepoint = u32::from(character);
                if codepoint <= 0xffff {
                    push_unicode_escape(codepoint, output);
                } else {
                    let supplementary = codepoint - 0x1_0000;
                    push_unicode_escape(0xd800 + (supplementary >> 10), output);
                    push_unicode_escape(0xdc00 + (supplementary & 0x3ff), output);
                }
            }
        }
    }
    output.push('"');
}

fn push_unicode_escape(codepoint: u32, output: &mut String) {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    output.push_str("\\u");
    for shift in [12, 8, 4, 0] {
        output.push(char::from(HEX[((codepoint >> shift) & 0x0f) as usize]));
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::python_canonical_json;

    #[test]
    fn matches_python_ascii_escaping_and_recursive_key_order() {
        let value = json!({
            "query": "café",
            "emoji": "🚀",
            "control": "\u{0001}",
            "nested": {"z": 1, "a": 2}
        });

        assert_eq!(
            python_canonical_json(&value),
            "{\"control\":\"\\u0001\",\"emoji\":\"\\ud83d\\ude80\",\"nested\":{\"a\":2,\"z\":1},\"query\":\"caf\\u00e9\"}"
        );
    }
}
