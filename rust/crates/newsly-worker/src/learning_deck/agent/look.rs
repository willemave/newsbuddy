//! `look` shows the model its slides as an upright iPhone reader sees them.
//!
//! Screens are rendered through the hosted viewer shell and returned as JPEG contact sheets.
//! A sheet budget per model conversation (the initial and repair phases of one attempt) bounds
//! request size, because every sheet stays in the conversation for the rest of the run.
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use base64::Engine as _;
use newsly_agent_runtime::{ImageMediaType, ToolDefinition, ToolImage, ToolOutput};
use newsly_e2b::{DirectE2bProvider, OutputLimits, SandboxHandle};
use schemars::{JsonSchema, schema_for};
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;

use crate::learning_deck::browser::hosted_viewer_html;
use crate::task_sandbox::{SandboxCommand, run_command};
use crate::task_tools::TaskToolExecutor;

/// Contact sheets (eight slides each) one model conversation may receive. At about 1.1 MP and
/// 100-250 KB per JPEG sheet, 24 stay far below provider image-count and request-size limits.
pub(super) const LOOK_SHEET_BUDGET: usize = 24;
const MAX_SHEETS_PER_CALL: usize = 5;
const SHEET_MAX_BYTES: usize = 1_000_000;
const INDEX_MAX_BYTES: usize = 10_000_000;
const LOOK_VIEWER: &str = "output/.newsly-look.html";
const LOOK_SCRIPT: &str = "/opt/deckkit/bin/look.mjs";
const LOOK_STATE_PENDING: &str = ".deckkit/look-state.pending.json";
const LOOK_STATE: &str = ".deckkit/look-state.json";
const MINIMUM_LOOK_TIME: Duration = Duration::from_secs(75);
const LOOK_RESERVE: Duration = Duration::from_secs(30);

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct LookArguments {
    /// Optional 1-based inclusive slide range such as "9-16" or "12". Without it, only slides
    /// that changed since the previous look are shown.
    #[serde(default)]
    range: Option<String>,
}

#[derive(Debug, Deserialize)]
struct LookManifest {
    slide_count: u64,
    captured: Vec<u64>,
    unchanged: u64,
    deferred: Vec<u64>,
    sheets: Vec<LookSheet>,
    page_errors: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct LookSheet {
    file: String,
    slides: Vec<u64>,
}

#[derive(Debug, Clone)]
pub(super) struct DeckLook {
    provider: Arc<DirectE2bProvider>,
    sandbox: SandboxHandle,
    model_tools: TaskToolExecutor,
    workspace: String,
    deadline: Instant,
    cancellation: CancellationToken,
    sheets_used: Arc<AtomicUsize>,
}

impl DeckLook {
    pub(super) fn new(
        provider: Arc<DirectE2bProvider>,
        sandbox: SandboxHandle,
        model_tools: TaskToolExecutor,
        workspace: String,
        deadline: Instant,
        cancellation: CancellationToken,
        sheets_used: Arc<AtomicUsize>,
    ) -> Self {
        Self {
            provider,
            sandbox,
            model_tools: model_tools.until(deadline),
            workspace,
            deadline,
            cancellation,
            sheets_used,
        }
    }

    pub(super) fn definition() -> ToolDefinition {
        ToolDefinition {
            name: "look".to_owned(),
            description: format!(
                "Screenshot the current deck as an upright iPhone reader sees it (each slide in its final fragment state) and return contact sheets of eight slides. Without a range, only slides changed since your last look are shown. One generation may receive {LOOK_SHEET_BUDGET} sheets in total, so look after meaningful batches of edits."
            ),
            input_schema: schema_for!(LookArguments),
        }
    }

    pub(super) async fn execute(&self, arguments: Value) -> ToolOutput {
        match self.look(arguments).await {
            Ok(output) => output,
            Err(message) => failure(&message),
        }
    }

    async fn look(&self, arguments: Value) -> Result<ToolOutput, String> {
        let arguments = serde_json::from_value::<LookArguments>(arguments)
            .map_err(|error| format!("look takes an optional range: {error}"))?;
        let range = arguments
            .range
            .map(|range| parse_range(&range))
            .transpose()?;
        let remaining = LOOK_SHEET_BUDGET.saturating_sub(self.sheets_used.load(Ordering::SeqCst));
        if remaining == 0 {
            return Err(format!(
                "the {LOOK_SHEET_BUDGET}-sheet look budget for this generation is used up; rely on validate_deck and your own reading of the HTML"
            ));
        }
        if self.deadline.saturating_duration_since(Instant::now()) < MINIMUM_LOOK_TIME {
            return Err("not enough time remains to render slides; finish and validate".to_owned());
        }
        let index = self
            .model_tools
            .read_text("output/index.html", INDEX_MAX_BYTES)
            .await
            .map_err(|error| format!("could not read output/index.html: {error}"))?;
        self.model_tools
            .write_bytes_typed(LOOK_VIEWER, hosted_viewer_html(&index).into_bytes())
            .await
            .map_err(|error| format!("could not prepare the viewer: {error}"))?;

        let per_call = remaining.min(MAX_SHEETS_PER_CALL);
        let mut args = vec![
            LOOK_SCRIPT.to_owned(),
            LOOK_VIEWER.to_owned(),
            per_call.to_string(),
        ];
        args.extend(range);
        let result = run_command(
            &self.provider,
            &self.sandbox,
            SandboxCommand {
                command: "/usr/bin/env".to_owned(),
                args: std::iter::once("node".to_owned()).chain(args).collect(),
                cwd: Some(self.workspace.clone()),
                deadline: (self.deadline - LOOK_RESERVE)
                    .min(Instant::now() + Duration::from_secs(150)),
                idle_timeout: Duration::from_secs(60),
                output_limits: OutputLimits {
                    stdout_bytes: 20_000,
                    stderr_bytes: 20_000,
                    combined_bytes: 40_000,
                    event_bytes: 22_000,
                    channel_capacity: 16,
                },
            },
            self.cancellation.child_token(),
        )
        .await
        .map_err(|error| format!("slide rendering failed: {error}"))?;
        if result.exit_code != 0 {
            return Err(format!(
                "slide rendering failed: {}",
                truncate(&result.output.stderr, 1_500)
            ));
        }
        let manifest = result
            .output
            .stdout
            .lines()
            .rev()
            .find_map(|line| serde_json::from_str::<LookManifest>(line).ok())
            .ok_or_else(|| "slide rendering returned no manifest".to_owned())?;

        check_manifest(&manifest, per_call)?;
        let mut images = Vec::with_capacity(manifest.sheets.len());
        for sheet in &manifest.sheets {
            let bytes = self
                .model_tools
                .read_bytes(&sheet.file, SHEET_MAX_BYTES)
                .await
                .map_err(|error| format!("could not read a contact sheet: {error}"))?;
            images.push(sheet_image(&bytes)?);
        }
        // Only now has the model actually received these slides; a failure above leaves them
        // unseen so the next look shows them again.
        self.commit_seen_state().await;
        let used = self.sheets_used.fetch_add(images.len(), Ordering::SeqCst) + images.len();
        Ok(ToolOutput {
            content: json!({
                "status": "rendered",
                "slide_count": manifest.slide_count,
                "shown": manifest.captured,
                "sheets": manifest.sheets.iter().map(|sheet| &sheet.slides).collect::<Vec<_>>(),
                "unchanged_slides": manifest.unchanged,
                "not_shown_yet": manifest.deferred,
                "page_errors": manifest.page_errors,
                "sheets_remaining": LOOK_SHEET_BUDGET.saturating_sub(used),
            }),
            is_error: false,
            images,
        })
    }
}

impl DeckLook {
    async fn commit_seen_state(&self) {
        let _ = run_command(
            &self.provider,
            &self.sandbox,
            SandboxCommand {
                command: "/bin/mv".to_owned(),
                args: vec![
                    "-f".to_owned(),
                    "--".to_owned(),
                    LOOK_STATE_PENDING.to_owned(),
                    LOOK_STATE.to_owned(),
                ],
                cwd: Some(self.workspace.clone()),
                deadline: self.deadline.min(Instant::now() + Duration::from_secs(15)),
                idle_timeout: Duration::from_secs(15),
                output_limits: OutputLimits {
                    stdout_bytes: 2_000,
                    stderr_bytes: 2_000,
                    combined_bytes: 4_000,
                    event_bytes: 4_000,
                    channel_capacity: 4,
                },
            },
            self.cancellation.child_token(),
        )
        .await;
    }
}

fn check_manifest(manifest: &LookManifest, per_call: usize) -> Result<(), String> {
    if manifest.sheets.len() > per_call {
        return Err("slide rendering returned more sheets than requested".to_owned());
    }
    if manifest.sheets.iter().any(|sheet| {
        !sheet.file.starts_with(".deckkit/look/sheet-")
            || std::path::Path::new(&sheet.file)
                .extension()
                .is_none_or(|extension| extension != "jpg")
            || sheet.file.contains("..")
    }) {
        return Err("slide rendering returned an unexpected sheet path".to_owned());
    }
    Ok(())
}

/// A sheet the provider would reject would poison every later request in the conversation,
/// so only real JPEG bytes become images.
fn sheet_image(bytes: &[u8]) -> Result<ToolImage, String> {
    if !bytes.starts_with(&[0xFF, 0xD8, 0xFF]) {
        return Err("slide rendering produced a contact sheet that is not a JPEG".to_owned());
    }
    Ok(ToolImage {
        media_type: ImageMediaType::Jpeg,
        data_base64: base64::engine::general_purpose::STANDARD.encode(bytes),
    })
}

fn parse_range(range: &str) -> Result<String, String> {
    let range = range.trim();
    let mut bounds = range.splitn(2, '-').map(str::parse::<u16>);
    let from = bounds.next().and_then(Result::ok);
    let to = bounds.next().map(Result::ok);
    match (from, to) {
        (Some(from), None) if from > 0 => Ok(from.to_string()),
        (Some(from), Some(Some(to))) if from > 0 && from <= to => Ok(format!("{from}-{to}")),
        _ => Err(format!(
            "range must look like \"9-16\" or \"12\", got {range:?}"
        )),
    }
}

fn truncate(text: &str, maximum: usize) -> String {
    text.chars().take(maximum).collect()
}

fn failure(message: &str) -> ToolOutput {
    ToolOutput {
        content: json!({"status": "failed", "error": message}),
        is_error: true,
        images: Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ranges_are_bounded_numeric_and_ordered() {
        assert_eq!(parse_range("9-16").unwrap(), "9-16");
        assert_eq!(parse_range(" 12 ").unwrap(), "12");
        for bad in [
            "",
            "0",
            "16-9",
            "1-",
            "a-b",
            "1-2-3",
            "--help",
            "1;rm -rf /",
        ] {
            assert!(parse_range(bad).is_err(), "{bad:?} must be rejected");
        }
    }

    #[test]
    fn arguments_are_closed() {
        assert!(serde_json::from_value::<LookArguments>(json!({})).is_ok());
        assert!(serde_json::from_value::<LookArguments>(json!({"range": "1-8"})).is_ok());
        assert!(serde_json::from_value::<LookArguments>(json!({"path": "/etc"})).is_err());
        assert_eq!(DeckLook::definition().name, "look");
    }

    #[test]
    fn manifests_are_bounded_to_requested_jpeg_sheets() {
        let manifest = |files: &[&str]| LookManifest {
            slide_count: 9,
            captured: vec![1],
            unchanged: 0,
            deferred: Vec::new(),
            sheets: files
                .iter()
                .map(|file| LookSheet {
                    file: (*file).to_owned(),
                    slides: vec![1],
                })
                .collect(),
            page_errors: Vec::new(),
        };
        assert!(check_manifest(&manifest(&[".deckkit/look/sheet-1.jpg"]), 1).is_ok());
        assert!(check_manifest(&manifest(&[".deckkit/look/sheet-1.jpg"; 2]), 1).is_err());
        for bad in [
            "output/index.html",
            ".deckkit/look/../x.jpg",
            ".deckkit/look/sheet-1.png",
            "/etc/passwd",
        ] {
            assert!(
                check_manifest(&manifest(&[bad]), 5).is_err(),
                "{bad} must be rejected"
            );
        }
        assert!(sheet_image(&[0xFF, 0xD8, 0xFF, 0xE0, 0x00]).is_ok());
        assert!(sheet_image(b"\x89PNG\r\n").is_err());
        assert!(sheet_image(&[]).is_err());
    }

    #[test]
    fn manifests_decode_the_vm_script_output() {
        let manifest: LookManifest = serde_json::from_str(
            r#"{"slide_count":12,"captured":[3,4],"unchanged":10,"deferred":[],"sheets":[{"file":".deckkit/look/sheet-1.jpg","slides":[3,4]}],"page_errors":[]}"#,
        )
        .unwrap();
        assert_eq!(manifest.captured, vec![3, 4]);
        assert_eq!(manifest.sheets[0].file, ".deckkit/look/sheet-1.jpg");
    }
}
