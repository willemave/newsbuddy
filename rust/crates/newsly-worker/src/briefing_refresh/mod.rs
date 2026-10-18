mod category_routing;
mod config;
mod filtering;
mod finalizer;
mod handler;
mod normalize;
mod planning;
mod semantic_lenses;
mod stale_assignment;

pub use config::{BriefingRefreshWorkerConfig, BriefingRefreshWorkerConfigError};
pub use handler::{BriefingRefreshHandler, BriefingRefreshWorkerServices};

mod warm_news;
pub use warm_news::PrepareNewsLensHandler;

mod reclustering;
pub use reclustering::ReclusterNewsLensesHandler;

const EMBEDDING_PROVIDER: &str = "openrouter";

fn split_model_spec(value: &str) -> (&str, &str) {
    value.split_once(':').unwrap_or(("unknown", value))
}

fn nonempty(value: &str) -> Option<&str> {
    (!value.trim().is_empty()).then_some(value)
}

fn centroid_model(model: &str) -> String {
    format!("{EMBEDDING_PROVIDER}:{model}")
}
