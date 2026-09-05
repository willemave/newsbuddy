use chrono::{DateTime, Datelike, Timelike, Utc, Weekday};

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum SchedulerJob {
    Scrape,
    IntegrationSync,
    QueueWatchdog,
    BriefingSweepReconcile,
    FeedDiscovery,
    TerminalTaskCleanup,
}

impl SchedulerJob {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Scrape => "scrape",
            Self::IntegrationSync => "integration_sync",
            Self::QueueWatchdog => "queue_watchdog",
            Self::BriefingSweepReconcile => "briefing_sweep_reconcile",
            Self::FeedDiscovery => "feed_discovery",
            Self::TerminalTaskCleanup => "terminal_task_cleanup",
        }
    }

    pub fn is_due(self, minute: DateTime<Utc>) -> bool {
        let minute_of_hour = minute.minute();
        match self {
            Self::Scrape | Self::IntegrationSync => minute_of_hour.is_multiple_of(15),
            Self::QueueWatchdog => minute_of_hour.is_multiple_of(5),
            Self::BriefingSweepReconcile => minute_of_hour == 0,
            Self::FeedDiscovery => {
                minute.weekday() == Weekday::Mon && minute.hour() == 3 && minute_of_hour == 0
            }
            Self::TerminalTaskCleanup => minute.hour() == 4 && minute_of_hour == 45,
        }
    }

    /// Only the latest missed occurrence is eligible; durable tick records deduplicate it.
    pub fn latest_due(self, now: DateTime<Utc>) -> DateTime<Utc> {
        let mut candidate = minute_bucket(now);
        while !self.is_due(candidate) {
            candidate -= chrono::Duration::minutes(1);
        }
        candidate
    }

    pub fn advisory_key(self, minute: DateTime<Utc>) -> String {
        format!(
            "newsly:scheduler:{}:{}",
            self.as_str(),
            minute.format("%Y%m%d%H%M")
        )
    }
}

pub(crate) const JOBS: [SchedulerJob; 6] = [
    SchedulerJob::Scrape,
    SchedulerJob::IntegrationSync,
    SchedulerJob::QueueWatchdog,
    SchedulerJob::BriefingSweepReconcile,
    SchedulerJob::FeedDiscovery,
    SchedulerJob::TerminalTaskCleanup,
];

pub(crate) fn minute_bucket(now: DateTime<Utc>) -> DateTime<Utc> {
    now.with_second(0)
        .and_then(|value| value.with_nanosecond(0))
        .expect("zero seconds and nanoseconds are valid")
}

#[cfg(test)]
pub(crate) fn due_jobs(minute: DateTime<Utc>) -> Vec<SchedulerJob> {
    JOBS.into_iter().filter(|job| job.is_due(minute)).collect()
}

#[cfg(test)]
mod tests {
    use chrono::{TimeZone, Utc};

    use super::*;

    #[test]
    fn production_schedules_match_the_legacy_utc_crontab() {
        let monday = Utc.with_ymd_and_hms(2026, 8, 31, 3, 0, 42).unwrap();
        let due = due_jobs(minute_bucket(monday));
        assert!(due.contains(&SchedulerJob::Scrape));
        assert!(due.contains(&SchedulerJob::IntegrationSync));
        assert!(due.contains(&SchedulerJob::QueueWatchdog));
        assert!(due.contains(&SchedulerJob::BriefingSweepReconcile));
        assert!(due.contains(&SchedulerJob::FeedDiscovery));

        let cleanup = Utc.with_ymd_and_hms(2026, 8, 31, 4, 45, 59).unwrap();
        let due = due_jobs(minute_bucket(cleanup));
        assert!(due.contains(&SchedulerJob::TerminalTaskCleanup));
        assert!(due.contains(&SchedulerJob::Scrape));
    }

    #[test]
    fn off_schedule_minute_only_runs_no_jobs() {
        let minute = Utc.with_ymd_and_hms(2026, 8, 30, 7, 13, 0).unwrap();
        assert!(due_jobs(minute).is_empty());
    }
}

#[cfg(test)]
mod recovery_tests {
    use super::*;
    use chrono::TimeZone;
    #[test]
    fn restart_selects_one_latest_occurrence() {
        let now = Utc.with_ymd_and_hms(2026, 9, 4, 7, 13, 0).unwrap();
        assert_eq!(
            SchedulerJob::FeedDiscovery.latest_due(now),
            Utc.with_ymd_and_hms(2026, 8, 31, 3, 0, 0).unwrap()
        );
        assert_eq!(
            SchedulerJob::TerminalTaskCleanup.latest_due(now),
            Utc.with_ymd_and_hms(2026, 9, 4, 4, 45, 0).unwrap()
        );
        assert_eq!(
            SchedulerJob::Scrape.latest_due(now),
            Utc.with_ymd_and_hms(2026, 9, 4, 7, 0, 0).unwrap()
        );
    }
}
