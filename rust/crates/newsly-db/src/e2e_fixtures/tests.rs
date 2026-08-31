use chrono::Utc;

use super::{
    DETAIL_TITLE, IosE2eFixtureNamespace, completed_chat_transcript, metadata::detail_metadata,
};

#[test]
fn namespace_accepts_bounded_shell_safe_values() {
    for value in [
        "local",
        "ios-17",
        "e2e2",
        "a2345678901234567890123456789012",
    ] {
        assert_eq!(
            IosE2eFixtureNamespace::parse(value)
                .expect("valid namespace")
                .as_str(),
            value
        );
    }
}

#[test]
fn namespace_rejects_paths_whitespace_uppercase_and_overflow() {
    for value in [
        "",
        " local",
        "UPPER",
        "-leading",
        "path/escape",
        "a23456789012345678901234567890123",
    ] {
        assert!(IosE2eFixtureNamespace::parse(value).is_err(), "{value:?}");
    }
}

#[test]
fn fixture_chat_uses_the_canonical_newsly_transcript() {
    let transcript = completed_chat_transcript(Utc::now());
    transcript.validate().expect("fixture transcript is valid");
    let value = serde_json::to_value(transcript).expect("serialize transcript");
    assert_eq!(value["version"], 1);
    assert_eq!(value["messages"][0]["role"], "user");
    assert_eq!(value["messages"][1]["role"], "assistant");
}

#[test]
fn metadata_keeps_the_longform_contract_fields_used_by_ios() {
    let metadata = detail_metadata("local");
    assert_eq!(metadata["summary_kind"], "long_structured");
    assert_eq!(metadata["summary_version"], 1);
    assert_eq!(metadata["summary"]["title"], DETAIL_TITLE);
    assert_eq!(
        metadata["summary"]["quotes"].as_array().map(Vec::len),
        Some(1)
    );
    assert!(
        metadata["summary"]["full_markdown"]
            .as_str()
            .is_some_and(|value| value.contains("Notable Quotes"))
    );
}
