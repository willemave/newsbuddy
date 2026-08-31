//! Native generated-image worker.
//!
//! Each attempt snapshots prompt input in one short transaction, performs provider and image
//! transformation work without a database connection, then publishes metadata, usage, and local
//! files only inside the queue kernel's exact-lease finalization fence.

mod finalizer;
mod handler;
mod model;
mod prompt;
mod repository;
mod storage;

pub use handler::{GenerateImageHandler, ImageWorkerServices};
pub use storage::{ImageFileStore, ImageFileStoreError};
