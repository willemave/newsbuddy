use std::collections::BTreeMap;
use std::ffi::OsStr;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;

use serde_json::Value;

use crate::error::{BootstrapError, Result};

const REQUIRED_TOOLS: [&str; 7] = ["bash", "python", "node", "git", "curl", "jq", "rg"];
const BROWSER_ERROR_CHAR_LIMIT: usize = 1_000;
const PLAYWRIGHT_PROBE: &str = r"
const fs = require('fs');
const { chromium } = require('playwright');
const executable = chromium.executablePath();
fs.accessSync(executable, fs.constants.R_OK | fs.constants.X_OK);
process.stdout.write(executable);
";

/// Build the sorted capability object consumed by sandbox acquisition.
pub fn probe_capabilities() -> BTreeMap<String, Value> {
    let mut capabilities = BTreeMap::new();
    for name in REQUIRED_TOOLS {
        capabilities.insert(
            name.to_owned(),
            find_executable(name).map_or(Value::Bool(false), |path| {
                Value::String(path.to_string_lossy().into_owned())
            }),
        );
    }

    let browser = capabilities
        .get("node")
        .and_then(Value::as_str)
        .map_or_else(
            || BrowserProbe {
                ready: false,
                error: Some("Node.js is unavailable".to_owned()),
            },
            |node| probe_browser(Path::new(node)),
        );
    capabilities.insert("playwright".to_owned(), Value::Bool(browser.ready));
    capabilities.insert("chromium".to_owned(), Value::Bool(browser.ready));
    if let Some(error) = browser.error {
        capabilities.insert("browser_validation_error".to_owned(), Value::String(error));
    }
    capabilities
}

/// Write exactly one JSON capability manifest followed by a newline.
pub fn write_capabilities(mut writer: impl Write) -> Result<()> {
    serde_json::to_writer(&mut writer, &probe_capabilities())
        .map_err(|error| BootstrapError::json("encoding capability manifest", error))?;
    writer
        .write_all(b"\n")
        .map_err(|error| BootstrapError::io("writing capability manifest", "stdout", error))?;
    writer
        .flush()
        .map_err(|error| BootstrapError::io("flushing capability manifest", "stdout", error))
}

#[derive(Debug)]
struct BrowserProbe {
    ready: bool,
    error: Option<String>,
}

fn probe_browser(node: &Path) -> BrowserProbe {
    match Command::new(node).arg("-e").arg(PLAYWRIGHT_PROBE).output() {
        Ok(output) if output.status.success() => BrowserProbe {
            ready: true,
            error: None,
        },
        Ok(output) => {
            let raw_error = if output.stderr.is_empty() {
                &output.stdout
            } else {
                &output.stderr
            };
            let detail = String::from_utf8_lossy(raw_error).trim().to_owned();
            let detail = if detail.contains("Cannot find module 'playwright'") {
                "Node Playwright package is unavailable".to_owned()
            } else if detail.is_empty() {
                format!(
                    "Playwright static browser check exited with {}",
                    output.status
                )
            } else {
                truncate_chars(&detail, BROWSER_ERROR_CHAR_LIMIT)
            };
            BrowserProbe {
                ready: false,
                error: Some(detail),
            }
        }
        Err(error) => BrowserProbe {
            ready: false,
            error: Some(truncate_chars(
                &format!("starting Playwright static browser check failed: {error}"),
                BROWSER_ERROR_CHAR_LIMIT,
            )),
        },
    }
}

fn find_executable(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    find_executable_in(name, &path)
}

fn find_executable_in(name: &str, path: &OsStr) -> Option<PathBuf> {
    std::env::split_paths(path)
        .map(|directory| directory.join(name))
        .find(|candidate| is_executable(candidate))
}

#[cfg(unix)]
fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;

    path.metadata()
        .is_ok_and(|metadata| metadata.is_file() && metadata.permissions().mode() & 0o111 != 0)
}

#[cfg(not(unix))]
fn is_executable(path: &Path) -> bool {
    path.is_file()
}

fn truncate_chars(value: &str, limit: usize) -> String {
    value.chars().take(limit).collect()
}

#[cfg(test)]
mod tests {
    use std::ffi::OsString;
    use std::fs;

    use tempfile::tempdir;

    use super::{PLAYWRIGHT_PROBE, find_executable_in, truncate_chars};

    #[test]
    #[cfg(unix)]
    fn executable_lookup_only_accepts_executable_files() {
        use std::os::unix::fs::PermissionsExt;

        let first = tempdir().expect("first tempdir");
        let second = tempdir().expect("second tempdir");
        let ignored = first.path().join("tool");
        let selected = second.path().join("tool");
        fs::write(&ignored, b"ignored").expect("write ignored fixture");
        fs::write(&selected, b"selected").expect("write selected fixture");
        fs::set_permissions(&selected, fs::Permissions::from_mode(0o755))
            .expect("mark fixture executable");
        let path = std::env::join_paths([first.path(), second.path()]).expect("join fixture PATH");

        assert_eq!(find_executable_in("tool", &path), Some(selected));
    }

    #[test]
    fn browser_probe_is_static() {
        assert!(PLAYWRIGHT_PROBE.contains("executablePath"));
        assert!(PLAYWRIGHT_PROBE.contains("accessSync"));
        assert!(!PLAYWRIGHT_PROBE.contains(".launch"));
    }

    #[test]
    fn text_truncation_respects_unicode_character_boundaries() {
        assert_eq!(truncate_chars("aé🙂z", 3), "aé🙂");
        let _portable_path_fixture = OsString::from("unused");
    }
}
