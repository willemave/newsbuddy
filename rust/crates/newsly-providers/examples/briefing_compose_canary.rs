//! Opt-in single-call canary. Reads an explicit captured request; never accesses the DB.
use newsly_providers::{
    BriefingCompositionGateway, BriefingCompositionRequest, BriefingCompositionSource,
};
use serde::Deserialize;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    lens_title: String,
    tier: String,
    sources: Vec<BriefingCompositionSource>,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args()
        .nth(1)
        .ok_or("explicit request JSON path required")?;
    let input: Input = serde_json::from_slice(&std::fs::read(path)?)?;
    let gateway = BriefingCompositionGateway::from_env()?;
    let started = std::time::Instant::now();
    // Exactly one invocation. Corrections require a separate explicit run.
    let result = gateway
        .compose(
            &BriefingCompositionRequest {
                lens_title: input.lens_title,
                tier: input.tier,
                sources: input.sources,
            },
            None,
        )
        .await;
    let observation = match result {
        Ok(generated) => {
            let rejection_code = generated
                .layout
                .as_ref()
                .err()
                .map(newsly_providers::BriefingLayoutRejection::code);
            serde_json::json!({
                "attempts": 1,
                "elapsed_ms": started.elapsed().as_millis(),
                "provider_succeeded": true,
                "validation": "canonical_provider_layout",
                "rejection_code": rejection_code,
                "accepted": generated.layout.is_ok(),
                "model": generated.model,
                "usage": generated.usage,
                "provider_response_id": generated.provider_response_id,
                "layout": generated.layout.ok(),
            })
        }
        Err(error) => serde_json::json!({
            "attempts": 1,
            "elapsed_ms": started.elapsed().as_millis(),
            "provider_succeeded": false,
            "accepted": false,
            "retryable": error.retryable(),
            "rejection_code": error.reason_code(),
            "usage": error.observed_usage(),
        }),
    };
    println!("{}", serde_json::to_string_pretty(&observation)?);
    Ok(())
}
