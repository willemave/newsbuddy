mod extraction;
mod firecrawl;
mod handlers;
mod model;
mod repository;
mod storage;

use url::Url;

pub use extraction::ContentExtractionRuntime;
pub(crate) use extraction::{ExtractionAttempt, FirecrawlDemandPolicy};
pub use firecrawl::FirecrawlClient;
pub use handlers::{AnalyzeUrlHandler, ContentWorkerServices, ProcessContentHandler};
pub(crate) use model::{FirecrawlUsage, UsageWrite};
pub(crate) use repository::{UsageInsert, insert_usage};
pub use storage::{ContentBodyStoreError, LocalContentBodyStore};

pub(crate) fn normalize_external_url(value: &str) -> Option<String> {
    let mut url = Url::parse(value.trim()).ok()?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return None;
    }
    url.set_scheme("https").ok()?;
    url.set_fragment(None);
    let host = url.host_str()?.to_ascii_lowercase();
    url.set_host(Some(&host)).ok()?;
    Some(url.to_string())
}
