use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::path::Path;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64;
use serde::{Deserialize, Serialize};
use tempfile::tempdir;
use url::Url;

use crate::error::{BootstrapError, Result};

pub const MAX_FEED_RESPONSE_BYTES: usize = 2_000_000;
const MAX_REQUEST_BYTES: usize = 256 * 1024;
const MAX_BATCH_URLS: usize = 32;
const MAX_PARALLEL_FETCHES: usize = 8;
const MAX_URL_BYTES: usize = 8 * 1024;
const MAX_HEADERS: usize = 64;
const MAX_HEADER_NAME_BYTES: usize = 128;
const MAX_HEADER_VALUE_BYTES: usize = 8 * 1024;
const MAX_TOTAL_HEADER_BYTES: usize = 64 * 1024;
const MAX_RAW_RESPONSE_HEADER_BYTES: usize = 256 * 1024;
const MAX_CURL_STDOUT_BYTES: usize = 16 * 1024;
const MAX_CURL_STDERR_BYTES: usize = 2_000;
const MAX_CONNECT_TIMEOUT_SECONDS: f64 = 10.0;
const MAX_REQUEST_TIMEOUT_SECONDS: f64 = 30.0;
const CURL_TIMEOUT_OVERHEAD: Duration = Duration::from_secs(5);
const DEFAULT_ACCEPT: &str = concat!(
    "text/html,application/xhtml+xml,application/rss+xml,",
    "application/atom+xml,application/xml;q=0.9,*/*;q=0.8"
);
const DEFAULT_USER_AGENT: &str = "NewslyFeedResearch/1.0";

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct FeedBatchRequest {
    urls: Vec<String>,
    #[serde(default)]
    headers: BTreeMap<String, String>,
    connect_timeout: NumberOrString,
    max_time: NumberOrString,
    max_bytes: usize,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(untagged)]
enum NumberOrString {
    Number(f64),
    String(String),
}

impl NumberOrString {
    fn parse(&self, field: &str) -> Result<f64> {
        let value = match self {
            Self::Number(value) => *value,
            Self::String(value) => value.parse::<f64>().map_err(|_| {
                BootstrapError::InvalidInput(format!("{field} must be a finite number"))
            })?,
        };
        if !value.is_finite() || value <= 0.0 {
            return Err(BootstrapError::InvalidInput(format!(
                "{field} must be greater than zero"
            )));
        }
        Ok(value)
    }
}

#[derive(Debug)]
struct ValidatedRequest {
    urls: Vec<String>,
    headers: BTreeMap<String, String>,
    connect_timeout: f64,
    max_time: f64,
    max_bytes: usize,
}

#[derive(Debug, Serialize)]
struct FeedBatchRow {
    index: usize,
    url: String,
    effective_url: String,
    status: u16,
    headers_b64: String,
    body_b64: String,
    curl_exit: i32,
    stderr: String,
}

/// Read a bounded feed request from stdin and emit one ordered JSONL row per URL.
pub fn fetch_batch(mut reader: impl Read, mut writer: impl Write) -> Result<()> {
    let request_bytes = read_bounded(&mut reader, MAX_REQUEST_BYTES, "feed batch request")?;
    let request: FeedBatchRequest = serde_json::from_slice(&request_bytes)
        .map_err(|error| BootstrapError::json("decoding feed batch request", error))?;
    let request = validate_request(request)?;
    let rows = execute_batch(&request)?;
    for row in rows {
        serde_json::to_writer(&mut writer, &row)
            .map_err(|error| BootstrapError::json("encoding feed batch row", error))?;
        writer
            .write_all(b"\n")
            .map_err(|error| BootstrapError::io("writing feed batch row", "stdout", error))?;
    }
    writer
        .flush()
        .map_err(|error| BootstrapError::io("flushing feed batch output", "stdout", error))
}

fn validate_request(mut request: FeedBatchRequest) -> Result<ValidatedRequest> {
    if request.urls.is_empty() {
        return Err(BootstrapError::InvalidInput(
            "feed batch must contain at least one URL".to_owned(),
        ));
    }
    if request.urls.len() > MAX_BATCH_URLS {
        return Err(BootstrapError::InvalidInput(format!(
            "feed batch contains more than {MAX_BATCH_URLS} URLs"
        )));
    }
    for value in &request.urls {
        validate_url(value)?;
    }

    add_default_header(&mut request.headers, "Accept", DEFAULT_ACCEPT);
    add_default_header(&mut request.headers, "User-Agent", DEFAULT_USER_AGENT);
    validate_headers(&request.headers)?;

    let connect_timeout = request.connect_timeout.parse("connect_timeout")?;
    if connect_timeout > MAX_CONNECT_TIMEOUT_SECONDS {
        return Err(BootstrapError::InvalidInput(format!(
            "connect_timeout exceeds {MAX_CONNECT_TIMEOUT_SECONDS} seconds"
        )));
    }
    let max_time = request.max_time.parse("max_time")?;
    if max_time > MAX_REQUEST_TIMEOUT_SECONDS {
        return Err(BootstrapError::InvalidInput(format!(
            "max_time exceeds {MAX_REQUEST_TIMEOUT_SECONDS} seconds"
        )));
    }
    if connect_timeout > max_time {
        return Err(BootstrapError::InvalidInput(
            "connect_timeout cannot exceed max_time".to_owned(),
        ));
    }
    if request.max_bytes == 0 || request.max_bytes > MAX_FEED_RESPONSE_BYTES {
        return Err(BootstrapError::InvalidInput(format!(
            "max_bytes must be between 1 and {MAX_FEED_RESPONSE_BYTES}"
        )));
    }

    Ok(ValidatedRequest {
        urls: request.urls,
        headers: request.headers,
        connect_timeout,
        max_time,
        max_bytes: request.max_bytes,
    })
}

fn validate_url(value: &str) -> Result<()> {
    if value.len() > MAX_URL_BYTES || value.chars().any(char::is_control) {
        return Err(BootstrapError::InvalidInput(
            "feed URL is empty, too long, or contains control characters".to_owned(),
        ));
    }
    let parsed = Url::parse(value)
        .map_err(|_| BootstrapError::InvalidInput("feed URL is invalid".to_owned()))?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host_str().is_none() {
        return Err(BootstrapError::InvalidInput(
            "feed URL must use HTTP or HTTPS and include a host".to_owned(),
        ));
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err(BootstrapError::InvalidInput(
            "feed URL cannot contain user credentials".to_owned(),
        ));
    }
    Ok(())
}

fn add_default_header(headers: &mut BTreeMap<String, String>, name: &str, value: &str) {
    if !headers
        .keys()
        .any(|candidate| candidate.eq_ignore_ascii_case(name))
    {
        headers.insert(name.to_owned(), value.to_owned());
    }
}

fn validate_headers(headers: &BTreeMap<String, String>) -> Result<()> {
    if headers.len() > MAX_HEADERS {
        return Err(BootstrapError::InvalidInput(format!(
            "feed request contains more than {MAX_HEADERS} headers"
        )));
    }
    let mut total_bytes = 0_usize;
    for (name, value) in headers {
        if name.is_empty()
            || name.len() > MAX_HEADER_NAME_BYTES
            || !name.bytes().all(is_header_name_byte)
        {
            return Err(BootstrapError::InvalidInput(
                "feed request contains an invalid header name".to_owned(),
            ));
        }
        if value.len() > MAX_HEADER_VALUE_BYTES
            || value
                .bytes()
                .any(|byte| byte == b'\r' || byte == b'\n' || byte == 0)
        {
            return Err(BootstrapError::InvalidInput(format!(
                "feed request header {name} has an invalid value"
            )));
        }
        total_bytes = total_bytes.saturating_add(name.len() + value.len() + 4);
    }
    if total_bytes > MAX_TOTAL_HEADER_BYTES {
        return Err(BootstrapError::InvalidInput(format!(
            "feed request headers exceed {MAX_TOTAL_HEADER_BYTES} bytes"
        )));
    }
    Ok(())
}

fn is_header_name_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric()
        || matches!(
            byte,
            b'!' | b'#'
                | b'$'
                | b'%'
                | b'&'
                | b'\''
                | b'*'
                | b'+'
                | b'-'
                | b'.'
                | b'^'
                | b'_'
                | b'`'
                | b'|'
                | b'~'
        )
}

fn execute_batch(request: &ValidatedRequest) -> Result<Vec<FeedBatchRow>> {
    let mut rows = Vec::with_capacity(request.urls.len());
    for (chunk_index, urls) in request.urls.chunks(MAX_PARALLEL_FETCHES).enumerate() {
        let offset = chunk_index * MAX_PARALLEL_FETCHES;
        let chunk_rows = thread::scope(|scope| {
            let handles = urls
                .iter()
                .enumerate()
                .map(|(index, url)| scope.spawn(move || fetch_one(offset + index, url, request)))
                .collect::<Vec<_>>();
            handles
                .into_iter()
                .map(|handle| handle.join().map_err(|_| BootstrapError::WorkerPanicked)?)
                .collect::<Result<Vec<_>>>()
        })?;
        rows.extend(chunk_rows);
    }
    Ok(rows)
}

fn fetch_one(index: usize, url: &str, request: &ValidatedRequest) -> Result<FeedBatchRow> {
    let scratch = tempdir().map_err(|error| {
        BootstrapError::io("creating feed fetch scratch directory", "/tmp", error)
    })?;
    let body_path = scratch.path().join("body");
    let headers_path = scratch.path().join("headers");

    let mut command = Command::new("curl");
    command
        .args([
            "--location",
            "--silent",
            "--show-error",
            "--compressed",
            "--proto",
            "=http,https",
            "--proto-redir",
            "=http,https",
            "--max-redirs",
            "10",
            "--connect-timeout",
        ])
        .arg(format_timeout(request.connect_timeout))
        .arg("--max-time")
        .arg(format_timeout(request.max_time))
        .arg("--max-filesize")
        .arg(request.max_bytes.to_string());
    for (name, value) in &request.headers {
        command.arg("--header").arg(format!("{name}: {value}"));
    }
    command
        .arg("--dump-header")
        .arg(&headers_path)
        .arg("--output")
        .arg(&body_path)
        .arg("--write-out")
        .arg("%{url_effective}\n%{http_code}")
        .arg("--url")
        .arg(url)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let outcome = run_curl(
        command,
        Duration::from_secs_f64(request.max_time) + CURL_TIMEOUT_OVERHEAD,
    )?;
    let mut curl_exit = if outcome.timed_out {
        28
    } else {
        outcome.status.code().unwrap_or(1)
    };
    let mut stderr = String::from_utf8_lossy(&outcome.stderr).into_owned();
    if outcome.stdout_truncated {
        curl_exit = 2;
        append_stderr(&mut stderr, "curl metadata exceeded its output bound");
    }

    let metadata = String::from_utf8_lossy(&outcome.stdout);
    let fields = metadata
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>();
    let effective_url = fields
        .get(fields.len().saturating_sub(2))
        .copied()
        .unwrap_or(url)
        .to_owned();
    let status = fields
        .last()
        .and_then(|value| value.parse::<u16>().ok())
        .unwrap_or(0);

    let (body, body_truncated) = read_optional_file_bounded(&body_path, request.max_bytes)?;
    if body_truncated {
        curl_exit = 63;
        append_stderr(&mut stderr, "feed response exceeded max_bytes");
    }
    let (raw_headers, headers_truncated) =
        read_optional_file_bounded(&headers_path, MAX_RAW_RESPONSE_HEADER_BYTES)?;
    if headers_truncated {
        curl_exit = 63;
        append_stderr(
            &mut stderr,
            "feed response headers exceeded their output bound",
        );
    }

    Ok(FeedBatchRow {
        index,
        url: url.to_owned(),
        effective_url,
        status,
        headers_b64: BASE64.encode(raw_headers),
        body_b64: BASE64.encode(body),
        curl_exit,
        stderr: truncate_tail_chars(stderr.trim(), MAX_CURL_STDERR_BYTES),
    })
}

#[derive(Debug)]
struct CurlOutcome {
    status: ExitStatus,
    stdout: Vec<u8>,
    stdout_truncated: bool,
    stderr: Vec<u8>,
    timed_out: bool,
}

fn run_curl(mut command: Command, timeout: Duration) -> Result<CurlOutcome> {
    let mut child = command
        .spawn()
        .map_err(|error| BootstrapError::io("starting curl", "curl", error))?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| BootstrapError::Process("curl stdout was unavailable".to_owned()))?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| BootstrapError::Process("curl stderr was unavailable".to_owned()))?;
    let stdout_reader = thread::spawn(move || read_prefix(stdout, MAX_CURL_STDOUT_BYTES));
    let stderr_reader = thread::spawn(move || read_tail(stderr, MAX_CURL_STDERR_BYTES));

    let (status, timed_out) = wait_with_timeout(&mut child, timeout)?;
    let (stdout, stdout_truncated) = stdout_reader
        .join()
        .map_err(|_| BootstrapError::WorkerPanicked)?
        .map_err(|error| BootstrapError::io("reading curl stdout", "curl", error))?;
    let stderr = stderr_reader
        .join()
        .map_err(|_| BootstrapError::WorkerPanicked)?
        .map_err(|error| BootstrapError::io("reading curl stderr", "curl", error))?;
    Ok(CurlOutcome {
        status,
        stdout,
        stdout_truncated,
        stderr,
        timed_out,
    })
}

fn wait_with_timeout(child: &mut Child, timeout: Duration) -> Result<(ExitStatus, bool)> {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(status) = child
            .try_wait()
            .map_err(|error| BootstrapError::io("polling curl", "curl", error))?
        {
            return Ok((status, false));
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let status = child
                .wait()
                .map_err(|error| BootstrapError::io("reaping curl", "curl", error))?;
            return Ok((status, true));
        }
        thread::sleep(Duration::from_millis(25));
    }
}

fn read_bounded(reader: &mut impl Read, limit: usize, label: &str) -> Result<Vec<u8>> {
    let mut bytes = Vec::with_capacity(limit.min(16 * 1024));
    reader
        .take((limit + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|error| BootstrapError::io("reading bounded input", label, error))?;
    if bytes.len() > limit {
        return Err(BootstrapError::InvalidInput(format!(
            "{label} exceeds {limit} bytes"
        )));
    }
    Ok(bytes)
}

fn read_optional_file_bounded(path: &Path, limit: usize) -> Result<(Vec<u8>, bool)> {
    let file = match std::fs::File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok((Vec::new(), false));
        }
        Err(error) => return Err(BootstrapError::io("opening curl output", path, error)),
    };
    let mut bytes = Vec::with_capacity(limit.min(16 * 1024));
    file.take((limit + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|error| BootstrapError::io("reading curl output", path, error))?;
    let truncated = bytes.len() > limit;
    bytes.truncate(limit);
    Ok((bytes, truncated))
}

fn read_prefix(mut reader: impl Read, limit: usize) -> std::io::Result<(Vec<u8>, bool)> {
    let mut output = Vec::with_capacity(limit);
    let mut truncated = false;
    let mut buffer = [0_u8; 8 * 1024];
    loop {
        let read = reader.read(&mut buffer)?;
        if read == 0 {
            return Ok((output, truncated));
        }
        let remaining = limit.saturating_sub(output.len());
        output.extend_from_slice(&buffer[..read.min(remaining)]);
        truncated |= read > remaining;
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

fn append_stderr(stderr: &mut String, detail: &str) {
    if !stderr.is_empty() && !stderr.ends_with('\n') {
        stderr.push('\n');
    }
    stderr.push_str(detail);
}

fn truncate_tail_chars(value: &str, limit: usize) -> String {
    let count = value.chars().count();
    value.chars().skip(count.saturating_sub(limit)).collect()
}

fn format_timeout(seconds: f64) -> String {
    let formatted = format!("{seconds:.6}");
    formatted
        .trim_end_matches('0')
        .trim_end_matches('.')
        .to_owned()
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::{
        FeedBatchRequest, MAX_BATCH_URLS, NumberOrString, format_timeout, validate_request,
    };

    fn request(urls: Vec<String>) -> FeedBatchRequest {
        FeedBatchRequest {
            urls,
            headers: BTreeMap::new(),
            connect_timeout: NumberOrString::String("10".to_owned()),
            max_time: NumberOrString::Number(30.0),
            max_bytes: 2_000_000,
        }
    }

    #[test]
    fn request_boundary_accepts_legacy_string_timeouts_and_adds_defaults() {
        let validated = validate_request(request(vec!["https://example.com/feed.xml".to_owned()]))
            .expect("valid request");

        assert!((validated.connect_timeout - 10.0).abs() < f64::EPSILON);
        assert!(validated.headers.contains_key("Accept"));
        assert!(validated.headers.contains_key("User-Agent"));
    }

    #[test]
    fn request_boundary_rejects_non_http_and_header_injection() {
        let error = validate_request(request(vec!["file:///etc/passwd".to_owned()]))
            .expect_err("file URL must fail");
        assert!(error.to_string().contains("HTTP or HTTPS"));

        let mut injected = request(vec!["https://example.com".to_owned()]);
        injected
            .headers
            .insert("X-Test".to_owned(), "ok\r\nInjected: yes".to_owned());
        let error = validate_request(injected).expect_err("header injection must fail");
        assert!(error.to_string().contains("invalid value"));
    }

    #[test]
    fn request_boundary_caps_batch_cardinality() {
        let urls = (0..=MAX_BATCH_URLS)
            .map(|index| format!("https://example.com/{index}"))
            .collect();
        assert!(validate_request(request(urls)).is_err());
    }

    #[test]
    fn timeout_format_matches_the_legacy_curl_contract() {
        assert_eq!(format_timeout(0.5), "0.5");
        assert_eq!(format_timeout(10.0), "10");
    }
}
