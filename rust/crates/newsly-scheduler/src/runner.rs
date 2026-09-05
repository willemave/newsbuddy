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

    async fn alert_watchdog(&self, report: crate::MaintenanceReport) {
        if report.touched() < usize::try_from(self.config.watchdog_alert_threshold).unwrap_or(1) {
            return;
        }
        let Some(webhook) = &self.config.watchdog_slack_webhook_url else {
            tracing::warn!(
                misrouted = report.misrouted,
                orphaned_leases = report.orphaned_leases,
                pipeline = ?report.pipeline,
                "queue watchdog found actionable pipeline state but no Slack webhook is configured"
            );
            return;
        };
        let message = format!(
            "Queue watchdog | attention={} move_misrouted={} recover_orphaned_leases={} expired_leases_left_claimable={} failing_sources={} overdue_tasks={} terminal_product_mismatches={}",
            report.touched(),
            report.misrouted,
            report.orphaned_leases,
            report.expired_reclaimable,
            report.pipeline.failing_sources,
            report.pipeline.overdue_tasks,
            report.pipeline.terminal_product_mismatches,
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
