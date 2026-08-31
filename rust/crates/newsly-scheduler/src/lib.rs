//! Native recurring-work scheduler for Newsly.
//!
//! Every due UTC minute is coordinated with a `PostgreSQL` advisory lock. Queue fan-out uses the
//! same `QueueKernel` as HTTP producers, so each task is validated, owner-stamped, user-locked,
//! and notified in the scheduler transaction. The maintenance watchdog deliberately does not
//! steal complete leases: valid expired leases are already reclaimable by queue consumers.

#![forbid(unsafe_code)]

mod config;
mod repository;
mod runner;
mod schedule;

pub use config::{SchedulerConfig, SchedulerConfigError, SchedulerLogFormat};
pub use repository::{
    MaintenanceReport, ScheduledJobReport, SchedulerRepository, SchedulerRepositoryError,
};
pub use runner::Scheduler;
pub use schedule::SchedulerJob;
