use std::time::Duration;

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::time::{Instant, sleep};

use crate::client::{ApiError, Client};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WaitOptions {
    pub interval: Duration,
    pub timeout: Duration,
}

impl WaitOptions {
    fn validate(self) -> Result<Self, ApiError> {
        if self.interval.is_zero() {
            return Err(ApiError::local("wait-interval must be greater than zero"));
        }
        Ok(self)
    }
}

pub fn normalize_status(status: &str) -> String {
    status.trim().to_ascii_lowercase()
}

pub fn is_terminal_status(status: &str) -> bool {
    matches!(
        normalize_status(status).as_str(),
        "completed" | "failed" | "skipped"
    )
}

pub fn is_failed_or_skipped_status(status: &str) -> bool {
    matches!(normalize_status(status).as_str(), "failed" | "skipped")
}

pub fn job_failed_or_skipped(job: &Value) -> bool {
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
    pub async fn wait_for_job(&self, job_id: i64, options: WaitOptions) -> Result<Value, ApiError> {
        let options = options.validate()?;
        let deadline = deadline(options.timeout)?;
        loop {
            let job = self.get_job(job_id).await?;
            let projection: JobProjection = project(&job, "job")?;
            if is_terminal_status(&projection.status) {
                return Ok(job);
            }
            if Instant::now() > deadline {
                return Err(ApiError::local_with_details(
                    format!("timed out waiting for job {job_id}"),
                    job,
                ));
            }
            sleep(options.interval).await;
        }
    }

    /// Poll until submitted content is fetchable or its submission fails.
    ///
    /// # Errors
    ///
    /// Returns an error when polling fails, processing fails, or the deadline expires.
    pub async fn wait_for_submitted_content(
        &self,
        content_id: i64,
        options: WaitOptions,
    ) -> Result<Value, ApiError> {
        let options = options.validate()?;
        let deadline = deadline(options.timeout)?;
        loop {
            match self.get_content(content_id).await {
                Ok(content) => return Ok(content),
                Err(error) if error.is_status(reqwest::StatusCode::NOT_FOUND) => {}
                Err(error) => return Err(error),
            }

            let query = vec![("limit".to_owned(), "100".to_owned())];
            let response = self.list_content_submission_statuses(&query).await?;
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

            if Instant::now() > deadline {
                return Err(ApiError::local_with_details(
                    format!("timed out waiting for content {content_id} to become available"),
                    serde_json::json!({ "content_id": content_id }),
                ));
            }
            sleep(options.interval).await;
        }
    }

    /// Poll an onboarding run until it completes or fails.
    ///
    /// # Errors
    ///
    /// Returns an error when polling fails, the response is invalid, or the deadline expires.
    pub async fn wait_for_onboarding(
        &self,
        run_id: i64,
        options: WaitOptions,
    ) -> Result<Value, ApiError> {
        let options = options.validate()?;
        let deadline = deadline(options.timeout)?;
        loop {
            let run = self.get_onboarding(run_id).await?;
            let projection: OnboardingProjection = project(&run, "onboarding")?;
            if matches!(
                normalize_status(&projection.run_status).as_str(),
                "completed" | "failed"
            ) {
                return Ok(run);
            }
            if Instant::now() > deadline {
                return Err(ApiError::local_with_details(
                    format!("timed out waiting for onboarding run {run_id}"),
                    run,
                ));
            }
            sleep(options.interval).await;
        }
    }

    /// Poll a CLI-link session until it supplies an API key.
    ///
    /// # Errors
    ///
    /// Returns an error when polling fails, the link is unusable, or the deadline expires.
    pub async fn wait_for_cli_link(
        &self,
        session_id: &str,
        poll_token: &str,
        options: WaitOptions,
    ) -> Result<Value, ApiError> {
        let options = options.validate()?;
        let deadline = deadline(options.timeout)?;
        loop {
            let response = self.poll_cli_link(session_id, poll_token).await?;
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
            if Instant::now() > deadline {
                return Err(ApiError::local("timed out waiting for CLI approval"));
            }
            sleep(options.interval).await;
        }
    }
}

fn deadline(timeout: Duration) -> Result<Instant, ApiError> {
    Instant::now()
        .checked_add(timeout)
        .ok_or_else(|| ApiError::local("wait timeout is too large"))
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
    use super::{is_failed_or_skipped_status, is_terminal_status, job_failed_or_skipped};
    use serde_json::json;

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
}
