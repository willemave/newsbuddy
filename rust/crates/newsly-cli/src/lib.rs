//! User-facing command-line client for the Newsly public API.

#![forbid(unsafe_code)]

mod app;
pub mod args;
pub mod client;
pub mod config;
pub mod library;
pub mod output;
pub mod wait;

pub use app::run;
