use std::future::Future;
use std::time::Duration;

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::time::{Instant, sleep, timeout_at};

use crate::client::{ApiError, Client};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct WaitOptions {
    pub(crate) interval: Duration,
    pub(crate) timeout: Duration,
}

impl WaitOptions {
    pub(crate) fn new(interval: Duration, timeout: Duration) -> Result<Self, ApiError> {
        if interval.is_zero() {
            return Err(ApiError::local("wait-interval must be greater than zero"));
        }
        Ok(Self { interval, timeout })
    }
}

pub(crate) fn normalize_status(status: &str) -> String {
    status.trim().to_ascii_lowercase()
}

pub(crate) fn is_terminal_status(status: &str) -> bool {
    matches!(
        normalize_status(status).as_str(),
        "completed" | "failed" | "skipped"
    )
}

pub(crate) fn is_failed_or_skipped_status(status: &str) -> bool {
    matches!(normalize_status(status).as_str(), "failed" | "skipped")
}

pub(crate) fn job_failed_or_skipped(job: &Value) -> bool {
    job.get("status")
        .and_then(Value::as_str)
        .is_some_and(is_failed_or_skipped_status)
}

impl Client {
    /// Poll a job until it reaches a terminal state.
    ///
    /// # Errors
    ///
    /// Returns an error when polling fails, the response is invalid, or the deadline expires.
    pub(crate) async fn wait_for_job(
        &self,
        job_id: i64,
        options: WaitOptions,
    ) -> Result<Value, ApiError> {
        let deadline = deadline(options.timeout)?;
        self.wait_for_job_until(job_id, options.interval, deadline)
            .await
    }

    pub(crate) async fn wait_for_job_until(
        &self,
        job_id: i64,
        interval: Duration,
        deadline: Instant,
    ) -> Result<Value, ApiError> {
        let mut last_job = Value::Null;
        loop {
            let job = await_before_deadline(deadline, self.get_job(job_id), || {
                timeout_error_with_optional_details(
                    format!("timed out waiting for job {job_id}"),
                    last_job.clone(),
                )
            })
            .await?;
            let projection: JobProjection = project(&job, "job")?;
            if is_terminal_status(&projection.status) {
                return Ok(job);
            }
            last_job = job;
            sleep_before_deadline(deadline, interval, || {
                ApiError::local_with_details(
                    format!("timed out waiting for job {job_id}"),
                    last_job.clone(),
                )
            })
            .await?;
        }
    }

    /// Poll until submitted content is fetchable or its submission fails.
    ///
    /// # Errors
    ///
    /// Returns an error when polling fails, processing fails, or the deadline expires.
    pub(crate) async fn wait_for_submitted_content_until(
        &self,
        content_id: i64,
        interval: Duration,
        deadline: Instant,
    ) -> Result<Value, ApiError> {
        let timeout_error = || {
            ApiError::local_with_details(
                format!("timed out waiting for content {content_id} to become available"),
                serde_json::json!({ "content_id": content_id }),
            )
        };
        loop {
            match await_before_deadline(deadline, self.get_content(content_id), timeout_error).await
            {
                Ok(content) => return Ok(content),
                Err(error) if error.is_status(reqwest::StatusCode::NOT_FOUND) => {}
                Err(error) => return Err(error),
            }

            let query = vec![("limit".to_owned(), "100".to_owned())];
            let response = await_before_deadline(
                deadline,
                self.list_content_submission_statuses(&query),
                timeout_error,
            )
            .await?;
            let projection: SubmissionListProjection =
                project(&response, "submission status list")?;
            if let Some(submission) = projection
                .submissions
                .into_iter()
                .find(|submission| submission.id == content_id)
                && is_failed_or_skipped_status(&submission.status)
            {
                let normalized = normalize_status(&submission.status);
                let message = submission
                    .error_message
                    .as_deref()
                    .map(str::trim)
                    .filter(|message| !message.is_empty())
                    .map_or_else(
                        || format!("submission {content_id} {normalized}"),
                        str::to_owned,
                    );
                let details = serde_json::to_value(submission).map_err(|error| {
                    ApiError::local(format!("encode failed submission status: {error}"))
                })?;
                return Err(ApiError::local_with_details(message, details));
            }

            sleep_before_deadline(deadline, interval, timeout_error).await?;
        }
    }

    /// Poll an onboarding run until it completes or fails.
    ///
    /// # Errors
    ///
    /// Returns an error when polling fails, the response is invalid, or the deadline expires.
    pub(crate) async fn wait_for_onboarding(
        &self,
        run_id: i64,
        options: WaitOptions,
    ) -> Result<Value, ApiError> {
        let deadline = deadline(options.timeout)?;
        let mut last_run = Value::Null;
        loop {
            let run = await_before_deadline(deadline, self.get_onboarding(run_id), || {
                timeout_error_with_optional_details(
                    format!("timed out waiting for onboarding run {run_id}"),
                    last_run.clone(),
                )
            })
            .await?;
            let projection: OnboardingProjection = project(&run, "onboarding")?;
            if matches!(
                normalize_status(&projection.run_status).as_str(),
                "completed" | "failed"
            ) {
                return Ok(run);
            }
            last_run = run;
            sleep_before_deadline(deadline, options.interval, || {
                ApiError::local_with_details(
                    format!("timed out waiting for onboarding run {run_id}"),
                    last_run.clone(),
                )
            })
            .await?;
        }
    }

    /// Poll a CLI-link session until it supplies an API key.
    ///
    /// # Errors
    ///
    /// Returns an error when polling fails, the link is unusable, or the deadline expires.
    pub(crate) async fn wait_for_cli_link(
        &self,
        session_id: &str,
        poll_token: &str,
        options: WaitOptions,
    ) -> Result<Value, ApiError> {
        let deadline = deadline(options.timeout)?;
        let timeout_error = || ApiError::local("timed out waiting for CLI approval");
        loop {
            let response = await_before_deadline(
                deadline,
                self.poll_cli_link(session_id, poll_token),
                timeout_error,
            )
            .await?;
            let projection: CliLinkProjection = project(&response, "CLI link")?;
            match normalize_status(&projection.status).as_str() {
                "approved"
                    if projection
                        .api_key
                        .as_deref()
                        .is_some_and(|api_key| !api_key.is_empty()) =>
                {
                    return Ok(response);
                }
                "claimed" => {
                    return Err(ApiError::local("CLI link session was already claimed"));
                }
                "expired" => return Err(ApiError::local("CLI link session expired")),
                _ => {}
            }
            sleep_before_deadline(deadline, options.interval, timeout_error).await?;
        }
    }
}

pub(crate) fn deadline(timeout: Duration) -> Result<Instant, ApiError> {
    Instant::now()
        .checked_add(timeout)
        .ok_or_else(|| ApiError::local("wait timeout is too large"))
}

async fn await_before_deadline<T, F, E>(
    deadline: Instant,
    future: F,
    timeout_error: E,
) -> Result<T, ApiError>
where
    F: Future<Output = Result<T, ApiError>>,
    E: FnOnce() -> ApiError,
{
    if Instant::now() >= deadline {
        return Err(timeout_error());
    }
    match timeout_at(deadline, future).await {
        Ok(result) => result,
        Err(_) => Err(timeout_error()),
    }
}

async fn sleep_before_deadline<E>(
    deadline: Instant,
    interval: Duration,
    timeout_error: E,
) -> Result<(), ApiError>
where
    E: FnOnce() -> ApiError,
{
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        return Err(timeout_error());
    }
    sleep(interval.min(remaining)).await;
    if Instant::now() >= deadline {
        return Err(timeout_error());
    }
    Ok(())
}

fn timeout_error_with_optional_details(message: String, details: Value) -> ApiError {
    if details.is_null() {
        ApiError::local(message)
    } else {
        ApiError::local_with_details(message, details)
    }
}

fn project<T>(value: &Value, name: &str) -> Result<T, ApiError>
where
    T: DeserializeOwned,
{
    serde_json::from_value(value.clone())
        .map_err(|error| ApiError::local(format!("invalid {name} response: {error}")))
}

#[derive(Debug, Deserialize)]
struct JobProjection {
    status: String,
}

#[derive(Debug, Deserialize)]
struct OnboardingProjection {
    run_status: String,
}

#[derive(Debug, Deserialize)]
struct CliLinkProjection {
    status: String,
    api_key: Option<String>,
}

#[derive(Debug, Deserialize)]
struct SubmissionListProjection {
    submissions: Vec<SubmissionProjection>,
}

#[derive(Debug, Deserialize, Serialize)]
struct SubmissionProjection {
    id: i64,
    status: String,
    error_message: Option<String>,
    #[serde(flatten)]
    other: serde_json::Map<String, Value>,
}

#[cfg(test)]
mod tests {
    use super::{
        await_before_deadline, deadline, is_failed_or_skipped_status, is_terminal_status,
        job_failed_or_skipped, sleep_before_deadline,
    };
    use crate::client::ApiError;
    use serde_json::json;
    use std::time::Duration;
    use tokio::time::Instant;

    #[test]
    fn terminal_statuses_are_case_insensitive_and_trimmed() {
        assert!(is_terminal_status(" COMPLETED "));
        assert!(is_terminal_status("Failed"));
        assert!(is_terminal_status("skipped"));
        assert!(!is_terminal_status("running"));
    }

    #[test]
    fn failed_or_skipped_helper_reads_raw_job_json() {
        assert!(is_failed_or_skipped_status(" FAILED "));
        assert!(job_failed_or_skipped(&json!({"status": "skipped"})));
        assert!(!job_failed_or_skipped(&json!({"status": "completed"})));
        assert!(!job_failed_or_skipped(&json!({"status": {}})));
    }

    #[tokio::test]
    async fn wait_helpers_respect_deadline_without_overrun() {
        let start = Instant::now();
        let wait_deadline = deadline(Duration::from_millis(40)).unwrap();
        let error = await_before_deadline(
            wait_deadline,
            async {
                tokio::time::sleep(Duration::from_millis(250)).await;
                Ok::<_, ApiError>(json!({"status": "completed"}))
            },
            || ApiError::local("timed out"),
        )
        .await
        .unwrap_err();
        assert!(error.to_string().contains("timed out"));
        assert!(start.elapsed() < Duration::from_millis(180));

        let sleep_deadline = deadline(Duration::from_millis(30)).unwrap();
        let sleep_err = sleep_before_deadline(sleep_deadline, Duration::from_secs(2), || {
            ApiError::local("sleep timed out")
        })
        .await
        .unwrap_err();
        assert!(sleep_err.to_string().contains("sleep timed out"));
    }
}
