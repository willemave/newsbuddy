use std::sync::Arc;
use std::time::Duration;

use chrono::Utc;
use reqwest::Client;
use secrecy::ExposeSecret;
use tokio::sync::watch;
use tokio::time::{MissedTickBehavior, interval};

use crate::{
    SchedulerConfig, SchedulerJob, SchedulerRepository,
    schedule::{due_jobs, minute_bucket},
};

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
    /// Due jobs are intentionally re-evaluated during the whole scheduled minute. Their durable
    /// completion marker makes successful runs no-ops, while a transient failure can retry on the
    /// next poll without waiting for the next 15-minute/day/week boundary.
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
        let scheduled_for = minute_bucket(Utc::now());
        for job in due_jobs(scheduled_for) {
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
                        "scheduler job failed; it may retry during the current scheduled minute"
                    );
                }
            }
        }
    }

    async fn alert_watchdog(&self, report: crate::MaintenanceReport) {
        if report.touched() < usize::try_from(self.config.watchdog_alert_threshold).unwrap_or(1) {
            return;
        }
        let Some(webhook) = &self.config.watchdog_slack_webhook_url else {
            tracing::warn!(
                misrouted = report.misrouted,
                orphaned_leases = report.orphaned_leases,
                "queue watchdog repaired tasks but no Slack webhook is configured"
            );
            return;
        };
        let message = format!(
            "Queue watchdog repaired tasks | total={} move_misrouted={} recover_orphaned_leases={} expired_leases_left_claimable={}",
            report.touched(),
            report.misrouted,
            report.orphaned_leases,
            report.expired_reclaimable,
        );
        match self
            .http
            .post(webhook.expose_secret())
            .timeout(Duration::from_secs(10))
            .json(&serde_json::json!({"text": message}))
            .send()
            .await
        {
            Ok(response) if response.status().is_success() => {
                tracing::info!("queue watchdog Slack alert sent");
            }
            Ok(response) => {
                tracing::warn!(status = %response.status(), "queue watchdog Slack alert failed");
            }
            Err(error) => {
                tracing::warn!(error = %error, "queue watchdog Slack alert failed");
            }
        }
    }
}
