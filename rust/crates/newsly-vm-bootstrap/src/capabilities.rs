use std::collections::BTreeMap;
use std::ffi::OsStr;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::error::{BootstrapError, Result};

const REQUIRED_TOOLS: [&str; 7] = ["bash", "python", "node", "git", "curl", "jq", "rg"];
const BROWSER_PROBE_TIMEOUT: Duration = Duration::from_secs(20);
const BROWSER_OUTPUT_LIMIT: usize = 16 * 1024;
const BROWSER_ERROR_CHAR_LIMIT: usize = 1_000;
const PLAYWRIGHT_PROBE: &str = r"
const { chromium } = require('playwright');
(async () => {
  let browser;
  const timer = setTimeout(() => {
    console.error('Playwright browser probe timed out');
    process.exit(124);
  }, 18000);
  try {
    browser = await chromium.launch({headless: true});
    await browser.close();
    clearTimeout(timer);
    process.exit(0);
  } catch (error) {
    clearTimeout(timer);
    console.error(error);
    process.exit(1);
  }
})();
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
    match run_browser_probe(node) {
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
            } else if output.timed_out {
                "Playwright browser probe timed out".to_owned()
            } else if detail.is_empty() {
                format!("Playwright browser probe exited with {}", output.status)
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
            error: Some(truncate_chars(&error.to_string(), BROWSER_ERROR_CHAR_LIMIT)),
        },
    }
}

#[derive(Debug)]
struct BrowserOutput {
    status: ExitStatus,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
    timed_out: bool,
}

fn run_browser_probe(node: &Path) -> Result<BrowserOutput> {
    let mut child = Command::new(node)
        .arg("-e")
        .arg(PLAYWRIGHT_PROBE)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| BootstrapError::io("starting Playwright browser probe", node, error))?;

    let stdout = child.stdout.take().ok_or_else(|| {
        BootstrapError::Process("browser probe stdout was unavailable".to_owned())
    })?;
    let stderr = child.stderr.take().ok_or_else(|| {
        BootstrapError::Process("browser probe stderr was unavailable".to_owned())
    })?;
    let stdout_reader = thread::spawn(move || read_tail(stdout, BROWSER_OUTPUT_LIMIT));
    let stderr_reader = thread::spawn(move || read_tail(stderr, BROWSER_OUTPUT_LIMIT));

    let (status, timed_out) = wait_with_timeout(&mut child, BROWSER_PROBE_TIMEOUT)?;
    let stdout = stdout_reader
        .join()
        .map_err(|_| BootstrapError::WorkerPanicked)?
        .map_err(|error| BootstrapError::io("reading browser probe stdout", "node", error))?;
    let stderr = stderr_reader
        .join()
        .map_err(|_| BootstrapError::WorkerPanicked)?
        .map_err(|error| BootstrapError::io("reading browser probe stderr", "node", error))?;
    Ok(BrowserOutput {
        status,
        stdout,
        stderr,
        timed_out,
    })
}

fn wait_with_timeout(child: &mut Child, timeout: Duration) -> Result<(ExitStatus, bool)> {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(status) = child
            .try_wait()
            .map_err(|error| BootstrapError::io("polling browser probe", "node", error))?
        {
            return Ok((status, false));
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let status = child
                .wait()
                .map_err(|error| BootstrapError::io("reaping browser probe", "node", error))?;
            return Ok((status, true));
        }
        thread::sleep(Duration::from_millis(25));
    }
}

fn read_tail(mut reader: impl Read, limit: usize) -> std::io::Result<Vec<u8>> {
    let mut tail = Vec::with_capacity(limit);
    let mut buffer = [0_u8; 8 * 1024];
    loop {
        let read = reader.read(&mut buffer)?;
        if read == 0 {
            return Ok(tail);
        }
        if read >= limit {
            tail.clear();
            tail.extend_from_slice(&buffer[read - limit..read]);
            continue;
        }
        let overflow = tail.len().saturating_add(read).saturating_sub(limit);
        if overflow > 0 {
            tail.drain(..overflow);
        }
        tail.extend_from_slice(&buffer[..read]);
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

    use super::{find_executable_in, read_tail, truncate_chars};

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
    fn tail_reader_drains_and_keeps_only_the_bound() {
        assert_eq!(
            read_tail(&b"0123456789"[..], 4).expect("read tail"),
            b"6789"
        );
    }

    #[test]
    fn text_truncation_respects_unicode_character_boundaries() {
        assert_eq!(truncate_chars("aé🙂z", 3), "aé🙂");
        let _portable_path_fixture = OsString::from("unused");
    }
}
