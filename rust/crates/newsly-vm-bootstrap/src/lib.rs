//! Static, credential-free helper used inside Newsly-managed E2B templates.

#![forbid(unsafe_code)]
#![allow(clippy::missing_errors_doc)]

pub mod capabilities;
pub mod error;
pub mod feed;

pub use error::{BootstrapError, Result};
