//! User-facing command-line client for the Newsly public API.

#![forbid(unsafe_code)]

mod app;
mod args;
mod client;
mod config;
mod favorites;
mod library;
mod output;
mod wait;

pub use app::run;
