//! Native generated-image worker.
//!
//! Each attempt snapshots prompt input in one short transaction, performs provider and image
//! transformation work without a database connection, records parsed provider responses in short
//! accounting transactions, then publishes metadata and local files inside the exact-lease fence.

mod finalizer;
mod handler;
mod model;
mod prompt;
mod repository;
mod storage;

pub use handler::{GenerateImageHandler, ImageWorkerServices};
pub use storage::{ImageFileStore, ImageFileStoreError};
