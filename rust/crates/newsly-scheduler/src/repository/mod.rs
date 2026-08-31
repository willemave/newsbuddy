mod fanout;
mod locking;
mod maintenance;

use chrono::{DateTime, Utc};
use newsly_queue::QueueKernel;
use sqlx::{PgPool, Postgres, Transaction};
use thiserror::Error;

use crate::{SchedulerConfig, SchedulerJob};

pub use maintenance::MaintenanceReport;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScheduledJobReport {
    pub job: SchedulerJob,
    pub considered: usize,
    pub enqueued: usize,
    pub skipped: usize,
    pub detail: &'static str,
    pub maintenance: Option<MaintenanceReport>,
}

impl ScheduledJobReport {
    const fn skipped(job: SchedulerJob, detail: &'static str) -> Self {
        Self {
            job,
            considered: 0,
            enqueued: 0,
            skipped: 1,
            detail,
            maintenance: None,
        }
    }
}

#[derive(Debug, Clone)]
pub struct SchedulerRepository {
    pool: PgPool,
    queue: QueueKernel,
}

impl SchedulerRepository {
    pub fn new(pool: PgPool) -> Self {
        Self {
            queue: QueueKernel::new(pool.clone()),
            pool,
        }
    }

    /// Execute one queue-fan-out or watchdog tick under a transaction-scoped advisory lock.
    ///
    /// The durable `event_logs` completion marker commits with the queue or repair mutations. A
    /// replacement replica that acquires the lock later in the same minute therefore observes the
    /// completed marker even if every scheduled queue task has already finished.
    ///
    /// # Errors
    ///
    /// Returns a database or queue validation/ownership error. The entire tick rolls back.
    pub async fn run_transactional_job(
        &self,
        job: SchedulerJob,
        scheduled_for: DateTime<Utc>,
        config: &SchedulerConfig,
    ) -> Result<Option<ScheduledJobReport>, SchedulerRepositoryError> {
        let Some(mut transaction) = self.begin_tick(job, scheduled_for).await? else {
            return Ok(None);
        };
        let report = match job {
            SchedulerJob::Scrape => self.enqueue_scrape(&mut transaction, config).await?,
            SchedulerJob::IntegrationSync => {
                self.enqueue_integration_sync(&mut transaction, config)
                    .await?
            }
            SchedulerJob::QueueWatchdog => {
                let report = self
                    .repair_queue(&mut transaction, config.orphan_lease_grace)
                    .await?;
                ScheduledJobReport {
                    job,
                    considered: report.expired_reclaimable
                        + report.misrouted
                        + report.orphaned_leases,
                    enqueued: 0,
                    skipped: report.expired_reclaimable,
                    detail: "queue_repaired",
                    maintenance: Some(report),
                }
            }
            SchedulerJob::BriefingSweepReconcile => {
                self.enqueue_briefing_sweeps(&mut transaction).await?
            }
            SchedulerJob::FeedDiscovery => {
                self.enqueue_feed_discovery(&mut transaction, config)
                    .await?
            }
            SchedulerJob::AgentDataReconcile => {
                self.enqueue_agent_data_reconcile(&mut transaction).await?
            }
            SchedulerJob::TerminalTaskCleanup => {
                return Err(SchedulerRepositoryError::WrongExecutionMode(job));
            }
        };
        locking::mark_tick_completed(
            &mut transaction,
            job,
            scheduled_for,
            &config.instance_id,
            &report,
        )
        .await?;
        transaction.commit().await?;
        Ok(Some(report))
    }

    async fn begin_tick(
        &self,
        job: SchedulerJob,
        scheduled_for: DateTime<Utc>,
    ) -> Result<Option<Transaction<'static, Postgres>>, SchedulerRepositoryError> {
        locking::begin_tick(&self.pool, job, scheduled_for).await
    }
}

#[derive(Debug, Error)]
pub enum SchedulerRepositoryError {
    #[error("scheduler PostgreSQL operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("scheduler queue enqueue failed")]
    Queue(#[from] newsly_queue::QueueError),
    #[error("{0:?} must use the scheduler maintenance execution path")]
    WrongExecutionMode(SchedulerJob),
    #[error("scheduler duration exceeds PostgreSQL interval bounds")]
    DurationOutOfRange,
    #[error("scheduler maintenance lock was not held at release")]
    AdvisoryUnlockRejected,
}
