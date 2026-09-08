use std::sync::Arc;
use std::time::Duration;

use chrono::Utc;
use reqwest::Client;
use secrecy::ExposeSecret;
use tokio::sync::watch;
use tokio::time::{MissedTickBehavior, interval};

use crate::{SchedulerConfig, SchedulerJob, SchedulerRepository, schedule::JOBS};

#[derive(Debug)]
pub struct Scheduler {
    repository: SchedulerRepository,
    config: Arc<SchedulerConfig>,
    http: Client,
}

impl Scheduler {
    pub fn new(repository: SchedulerRepository, config: SchedulerConfig) -> Self {
        Self {
            repository,
            config: Arc::new(config),
            http: Client::new(),
        }
    }

    /// Run recurring UTC schedule evaluation until shutdown is requested.
    ///
    /// Each poll evaluates the latest scheduled occurrence, including after downtime. Durable
    /// completion markers suppress repeats, and transient failures retry on the next poll.
    /// Older missed occurrences are coalesced rather than replayed.
    pub async fn run(&self, mut shutdown: watch::Receiver<bool>) {
        let mut poll = interval(self.config.poll_interval);
        poll.set_missed_tick_behavior(MissedTickBehavior::Skip);
        loop {
            tokio::select! {
                _ = poll.tick() => {
                    if *shutdown.borrow() {
                        break;
                    }
                    self.run_due_jobs().await;
                }
                changed = shutdown.changed() => {
                    if changed.is_err() || *shutdown.borrow() {
                        break;
                    }
                }
            }
        }
    }

    async fn run_due_jobs(&self) {
        let now = Utc::now();
        for job in JOBS {
            let scheduled_for = job.latest_due(now);
            let result = if job == SchedulerJob::TerminalTaskCleanup {
                self.repository
                    .run_terminal_cleanup(scheduled_for, &self.config)
                    .await
            } else {
                self.repository
                    .run_transactional_job(job, scheduled_for, &self.config)
                    .await
            };
            match result {
                Ok(Some(report)) => {
                    tracing::info!(
                        job = report.job.as_str(),
                        scheduled_for = %scheduled_for,
                        considered = report.considered,
                        enqueued = report.enqueued,
                        skipped = report.skipped,
                        detail = report.detail,
                        "scheduler job completed"
                    );
                    if let Some(maintenance) = report.maintenance {
                        self.alert_watchdog(maintenance).await;
                    }
                }
                Ok(None) => {
                    tracing::debug!(
                        job = job.as_str(),
                        scheduled_for = %scheduled_for,
                        "scheduler job already completed or owned by another replica"
                    );
                }
                Err(error) => {
                    tracing::error!(
                        job = job.as_str(),
                        scheduled_for = %scheduled_for,
                        error = %error,
                        "scheduler job failed; latest occurrence will retry"
                    );
                }
            }
        }
    }

    async fn alert_watchdog(&self, _report: crate::MaintenanceReport) {
        use newsly_db::pipeline_monitoring::{claim_alert, set_signal, settle_alert};
        let pool = self.repository.pool();
        let configured = self.config.watchdog_slack_webhook_url.is_some();
        match pool.acquire().await {
            Ok(mut connection) => {
                if let Err(error) = set_signal(
                    &mut connection,
                    "alert_destination_missing",
                    !configured,
                    "Queue alert destination is not configured",
                )
                .await
                {
                    tracing::error!(%error, "cannot persist monitoring delivery health");
                }
            }
            Err(error) => {
                tracing::error!(%error, "cannot read alert delivery state");
                return;
            }
        }
        let Some(webhook) = &self.config.watchdog_slack_webhook_url else {
            return;
        };
        // Bounded work per tick; durable pending rows survive failure or restart.
        for _ in 0..8 {
            let alert = match claim_alert(pool).await {
                Ok(Some(alert)) => alert,
                Ok(None) => break,
                Err(error) => {
                    tracing::error!(%error, "cannot claim watchdog alert");
                    break;
                }
            };
            let state = if alert.active {
                "attention"
            } else {
                "recovered"
            };
            let message = format!(
                "Queue watchdog | {state} | {} | {}",
                alert.alert_key, alert.message
            );
            let error = deliver_alert(
                &self.http,
                webhook.expose_secret(),
                &message,
                Duration::from_secs(10),
            )
            .await;
            if let Err(error) = settle_alert(pool, &alert, error.as_deref()).await {
                tracing::error!(%error, "cannot settle watchdog alert");
            }
        }
    }
}

async fn deliver_alert(
    client: &Client,
    destination: &str,
    message: &str,
    timeout: Duration,
) -> Option<String> {
    match client
        .post(destination)
        .timeout(timeout)
        .json(&serde_json::json!({"text":message}))
        .send()
        .await
    {
        Ok(response) if response.status().is_success() => None,
        Ok(response) => Some(format!("http_{}", response.status().as_u16())),
        Err(_) => Some("transport_or_timeout".to_owned()),
    }
}

#[cfg(test)]
mod delivery_tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    #[tokio::test]
    async fn alert_receiver_success_throttle_failure_and_timeout() {
        for (status, delay, expected) in [
            (200, 0, None),
            (429, 0, Some("http_429")),
            (503, 0, Some("http_503")),
            (200, 200, Some("transport_or_timeout")),
        ] {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let address = listener.local_addr().unwrap();
            let receiver = tokio::spawn(async move {
                let (mut stream, _) = listener.accept().await.unwrap();
                let mut request = [0; 4096];
                let _ = stream.read(&mut request).await.unwrap();
                tokio::time::sleep(Duration::from_millis(delay)).await;
                let _ = stream.write_all(format!("HTTP/1.1 {status} Test\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").as_bytes()).await;
            });
            let error = deliver_alert(
                &Client::new(),
                &format!("http://{address}"),
                "test only",
                Duration::from_millis(100),
            )
            .await;
            assert_eq!(error.as_deref(), expected);
            receiver.await.unwrap();
        }
    }
}
