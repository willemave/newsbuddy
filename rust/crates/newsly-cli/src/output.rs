//! Stable machine- and human-readable CLI output envelopes.

use std::fmt;
use std::io::Write;

use clap::ValueEnum;
use serde::Serialize;
use serde_json::Value;
use thiserror::Error;

use crate::client::ApiError;

pub(crate) const FORMAT_JSON: &str = "json";
pub(crate) const FORMAT_TEXT: &str = "text";

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, ValueEnum)]
#[value(rename_all = "lower")]
pub(crate) enum OutputFormat {
    #[default]
    Json,
    Text,
}

impl fmt::Display for OutputFormat {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::Json => FORMAT_JSON,
            Self::Text => FORMAT_TEXT,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub(crate) struct Envelope {
    #[serde(skip_serializing_if = "String::is_empty")]
    pub(crate) command: String,
    pub(crate) ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) data: Option<Value>,
    /// Optional display body supplied by the command; never part of the JSON envelope.
    #[serde(skip)]
    pub(crate) text_data: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) job: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) error: Option<ApiError>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) config_path: Option<String>,
}

impl Envelope {
    pub(crate) fn success(
        command: impl Into<String>,
        data: Option<Value>,
        job: Option<Value>,
    ) -> Self {
        Self {
            command: command.into(),
            ok: true,
            data,
            text_data: None,
            job,
            error: None,
            config_path: None,
        }
    }

    pub(crate) fn failure(
        command: impl Into<String>,
        error: ApiError,
        config_path: Option<String>,
    ) -> Self {
        Self {
            command: command.into(),
            ok: false,
            data: None,
            text_data: None,
            job: None,
            error: Some(error),
            config_path,
        }
    }
}

#[derive(Debug, Error)]
pub(crate) enum OutputError {
    #[error("failed to encode CLI output: {0}")]
    Json(#[from] serde_json::Error),
    #[error("failed to write CLI output: {0}")]
    Io(#[from] std::io::Error),
}

/// Write an output envelope using the selected format.
///
/// # Errors
///
/// Returns an error when JSON encoding or writing fails.
pub(crate) fn emit(
    writer: &mut impl Write,
    envelope: &Envelope,
    format: OutputFormat,
) -> Result<(), OutputError> {
    match format {
        OutputFormat::Json => write_json_block(writer, envelope),
        OutputFormat::Text => emit_text(writer, envelope),
    }
}

fn emit_text(writer: &mut impl Write, envelope: &Envelope) -> Result<(), OutputError> {
    writeln!(writer, "command: {}", envelope.command)?;
    writeln!(writer, "ok: {}", envelope.ok)?;
    if let Some(text) = &envelope.text_data {
        writer.write_all(text.as_bytes())?;
    } else if let Some(data) = &envelope.data {
        write_json_block(writer, data)?;
    }
    if let Some(job) = &envelope.job {
        writeln!(writer, "job:")?;
        write_json_block(writer, job)?;
    }
    if let Some(error) = &envelope.error {
        writeln!(writer, "error:")?;
        write_json_block(writer, error)?;
    }
    Ok(())
}

fn write_json_block(writer: &mut impl Write, value: &impl Serialize) -> Result<(), OutputError> {
    serde_json::to_writer_pretty(&mut *writer, value)?;
    writeln!(writer)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn json_success_envelope_is_pretty_and_omits_absent_fields() {
        let envelope = Envelope::success(
            "content.submit",
            Some(json!({"content_id": 42})),
            Some(json!({"id": 7, "status": "pending"})),
        );
        let mut output = Vec::new();
        emit(&mut output, &envelope, OutputFormat::Json).expect("emit JSON");
        let output = String::from_utf8(output).expect("UTF-8 output");
        assert!(output.starts_with("{\n  \"command\": \"content.submit\","));
        assert!(output.ends_with('\n'));
        assert!(!output.contains("\"error\""));
        assert!(!output.contains("\"config_path\""));
        let decoded: Value = serde_json::from_str(&output).expect("decode envelope");
        assert_eq!(decoded["ok"], true);
        assert_eq!(decoded["data"]["content_id"], 42);
        assert_eq!(decoded["job"]["id"], 7);
    }

    #[test]
    fn json_error_envelope_preserves_typed_error_fields() {
        let error = ApiError {
            message: "conflict".to_owned(),
            status_code: Some(409),
            code: Some("already_exists".to_owned()),
            details: Some(Box::new(json!({"content_id": 42}))),
            retryable: Some(false),
            request_id: Some("request-1".to_owned()),
        };
        let envelope =
            Envelope::failure("sources.add", error, Some("/tmp/newsbuddy.json".to_owned()));
        let mut output = Vec::new();
        emit(&mut output, &envelope, OutputFormat::Json).expect("emit JSON");
        let decoded: Value = serde_json::from_slice(&output).expect("decode envelope");
        assert_eq!(decoded["ok"], false);
        assert_eq!(decoded["error"]["status_code"], 409);
        assert_eq!(decoded["error"]["retryable"], false);
        assert_eq!(decoded["config_path"], "/tmp/newsbuddy.json");
        assert!(decoded.get("data").is_none());
    }

    #[test]
    fn text_envelope_matches_legacy_layout() {
        let envelope = Envelope::success(
            "jobs.get",
            Some(json!({"id": 7})),
            Some(json!({"status": "completed"})),
        );
        let mut output = Vec::new();
        emit(&mut output, &envelope, OutputFormat::Text).expect("emit text");
        let output = String::from_utf8(output).expect("UTF-8 output");
        assert_eq!(
            output,
            "command: jobs.get\nok: true\n{\n  \"id\": 7\n}\njob:\n{\n  \"status\": \"completed\"\n}\n"
        );
    }

    #[test]
    fn text_error_uses_error_block() {
        let envelope = Envelope::failure("jobs.get", ApiError::local("network unavailable"), None);
        let mut output = Vec::new();
        emit(&mut output, &envelope, OutputFormat::Text).expect("emit text");
        let output = String::from_utf8(output).expect("UTF-8 output");
        assert!(output.starts_with("command: jobs.get\nok: false\nerror:\n"));
        assert!(output.contains("\"message\": \"network unavailable\""));
        assert!(!output.contains("status_code"));
    }

    #[test]
    fn command_text_body_is_used_only_for_text_output() {
        let mut envelope =
            Envelope::success("example", Some(serde_json::json!({"count": 1})), None);
        envelope.text_data = Some("One result\n".to_owned());
        let mut text = Vec::new();
        emit(&mut text, &envelope, OutputFormat::Text).unwrap();
        assert_eq!(
            String::from_utf8(text).unwrap(),
            "command: example\nok: true\nOne result\n"
        );
        let mut json = Vec::new();
        emit(&mut json, &envelope, OutputFormat::Json).unwrap();
        let json: Value = serde_json::from_slice(&json).unwrap();
        assert_eq!(json["data"]["count"], 1);
        assert!(json.get("text_data").is_none());
    }
}
