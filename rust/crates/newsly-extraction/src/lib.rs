//! Typed client for the private, database-free Python document extractor.
//!
//! This crate deliberately has no database dependency. Rust validates the initial public URL,
//! bounds request and response sizes, and receives a typed result; the caller owns persistence,
//! retries, Firecrawl fallback, and usage accounting.

#![forbid(unsafe_code)]

mod client;
mod error;
mod public_url;
mod types;

pub use client::{DocumentExtractorClient, DocumentExtractorConfig};
pub use error::ExtractionClientError;
pub use public_url::PublicUrl;
pub use types::{
    ExtractIntent, ExtractOptions, ExtractRequest, ExtractResult, ExtractionFailure,
    ExtractionFailureCode, ExtractionFallbackRequired, ExtractionMethod, ExtractionProfile,
    ExtractionSuccess, ExtractionTiming, FallbackKind, PubMedDelegation, TraceContext, UsageEvent,
};

pub const EXTRACTION_SCHEMA_VERSION: u16 = 1;
