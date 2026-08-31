use std::time::Duration;

use sqlx::postgres::PgListener;
use tokio::sync::watch;
use tokio::task::JoinHandle;
use tokio::time::{sleep, timeout};
use tracing::{debug, warn};

const QUEUE_NOTIFY_CHANNEL: &str = "processing_tasks";
const RECONNECT_BACKOFF_START: Duration = Duration::from_secs(1);
const RECONNECT_BACKOFF_MAX: Duration = Duration::from_secs(30);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QueueWakeOutcome {
    Notification,
    PollTimeout,
    ShuttingDown,
}

/// Process-wide `PostgreSQL` notification fan-out. One dedicated `LISTEN` connection wakes every
/// subscriber; workers always retain their ordinary polling deadline as the correctness fallback.
#[derive(Debug)]
pub struct QueueNotificationHub {
    generation_tx: watch::Sender<u64>,
    connected_tx: watch::Sender<bool>,
    stop_tx: watch::Sender<bool>,
    task: Option<JoinHandle<()>>,
}

impl QueueNotificationHub {
    pub fn spawn(database_url: impl Into<String>) -> Self {
        let database_url = database_url.into();
        let (generation_tx, _) = watch::channel(0_u64);
        let (connected_tx, _) = watch::channel(false);
        let (stop_tx, stop_rx) = watch::channel(false);
        let task_generation_tx = generation_tx.clone();
        let task_connected_tx = connected_tx.clone();
        let task = tokio::spawn(async move {
            notification_loop(database_url, task_generation_tx, task_connected_tx, stop_rx).await;
        });
        Self {
            generation_tx,
            connected_tx,
            stop_tx,
            task: Some(task),
        }
    }

    pub fn subscribe(&self) -> QueueNotificationWaiter {
        QueueNotificationWaiter {
            generation: self.generation_tx.subscribe(),
            connection_state: self.connected_tx.subscribe(),
            shutdown: self.stop_tx.subscribe(),
        }
    }

    /// Requests listener shutdown and waits for the dedicated connection task.
    pub async fn close(mut self) {
        let _ = self.stop_tx.send(true);
        if let Some(task) = self.task.take() {
            let _ = task.await;
        }
    }
}

impl Drop for QueueNotificationHub {
    fn drop(&mut self) {
        let _ = self.stop_tx.send(true);
        if let Some(task) = &self.task {
            task.abort();
        }
    }
}

#[derive(Debug, Clone)]
pub struct QueueNotificationWaiter {
    generation: watch::Receiver<u64>,
    connection_state: watch::Receiver<bool>,
    shutdown: watch::Receiver<bool>,
}

impl QueueNotificationWaiter {
    /// Waits for a worker wake-up, but never makes notification delivery a correctness
    /// requirement: unavailable or quiet listeners simply consume the polling interval.
    pub async fn wait(&mut self, poll_interval: Duration) -> QueueWakeOutcome {
        if *self.shutdown.borrow() {
            return QueueWakeOutcome::ShuttingDown;
        }
        let connected = *self.connection_state.borrow();
        if connected {
            self.wait_connected(poll_interval).await
        } else {
            self.wait_disconnected(poll_interval).await
        }
    }

    async fn wait_disconnected(&mut self, poll_interval: Duration) -> QueueWakeOutcome {
        tokio::select! {
            () = sleep(poll_interval) => QueueWakeOutcome::PollTimeout,
            changed = self.shutdown.changed() => {
                if changed.is_err() || *self.shutdown.borrow() {
                    QueueWakeOutcome::ShuttingDown
                } else {
                    QueueWakeOutcome::PollTimeout
                }
            }
            changed = self.connection_state.changed() => {
                if changed.is_ok() && *self.connection_state.borrow() {
                    QueueWakeOutcome::Notification
                } else {
                    QueueWakeOutcome::PollTimeout
                }
            }
        }
    }

    async fn wait_connected(&mut self, poll_interval: Duration) -> QueueWakeOutcome {
        let generation = *self.generation.borrow_and_update();
        match timeout(poll_interval, async {
            loop {
                tokio::select! {
                    changed = self.generation.changed() => {
                        if changed.is_err() || *self.generation.borrow_and_update() != generation {
                            return QueueWakeOutcome::Notification;
                        }
                    }
                    changed = self.connection_state.changed() => {
                        if changed.is_err() || !*self.connection_state.borrow() {
                            return QueueWakeOutcome::PollTimeout;
                        }
                    }
                    changed = self.shutdown.changed() => {
                        if changed.is_err() || *self.shutdown.borrow() {
                            return QueueWakeOutcome::ShuttingDown;
                        }
                    }
                }
            }
        })
        .await
        {
            Ok(outcome) => outcome,
            Err(_) => QueueWakeOutcome::PollTimeout,
        }
    }
}

async fn notification_loop(
    database_url: String,
    generation_tx: watch::Sender<u64>,
    connected_tx: watch::Sender<bool>,
    mut stop_rx: watch::Receiver<bool>,
) {
    let mut backoff = RECONNECT_BACKOFF_START;
    loop {
        if *stop_rx.borrow() {
            return;
        }
        match PgListener::connect(&database_url).await {
            Ok(mut listener) => {
                if let Err(error) = listener.listen(QUEUE_NOTIFY_CHANNEL).await {
                    warn!(error = %error, "unable to LISTEN for queue notifications; polling only");
                    set_connected(&connected_tx, &generation_tx, false);
                } else {
                    set_connected(&connected_tx, &generation_tx, true);
                    backoff = RECONNECT_BACKOFF_START;
                    let disconnected =
                        consume_notifications(&mut listener, &generation_tx, &mut stop_rx).await;
                    set_connected(&connected_tx, &generation_tx, false);
                    if !disconnected {
                        return;
                    }
                }
            }
            Err(error) => {
                warn!(error = %error, "unable to open queue notification listener; polling only");
                set_connected(&connected_tx, &generation_tx, false);
            }
        }

        tokio::select! {
            () = sleep(backoff) => {}
            changed = stop_rx.changed() => {
                if changed.is_err() || *stop_rx.borrow() {
                    return;
                }
            }
        }
        backoff = (backoff * 2).min(RECONNECT_BACKOFF_MAX);
    }
}

async fn consume_notifications(
    listener: &mut PgListener,
    generation_tx: &watch::Sender<u64>,
    stop_rx: &mut watch::Receiver<bool>,
) -> bool {
    loop {
        tokio::select! {
            notification = listener.recv() => {
                match notification {
                    Ok(notification) => {
                        debug!(payload = notification.payload(), "processing-task notification received");
                        increment_generation(generation_tx);
                    }
                    Err(error) => {
                        warn!(error = %error, "queue notification connection failed; reconnecting");
                        return true;
                    }
                }
            }
            changed = stop_rx.changed() => {
                if changed.is_err() || *stop_rx.borrow() {
                    return false;
                }
            }
        }
    }
}

fn set_connected(
    connected_tx: &watch::Sender<bool>,
    generation_tx: &watch::Sender<u64>,
    connected: bool,
) {
    connected_tx.send_replace(connected);
    if !connected {
        increment_generation(generation_tx);
    }
}

fn increment_generation(generation_tx: &watch::Sender<u64>) {
    generation_tx.send_modify(|generation| *generation = generation.wrapping_add(1));
}
