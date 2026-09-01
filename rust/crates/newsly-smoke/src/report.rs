use chrono::{DateTime, Utc};
use serde::Serialize;
use serde_json::{Map, Value};

#[derive(Debug, Clone, Serialize)]
pub(crate) struct SmokeReport {
    pub schema_version: u8,
    pub run_id: String,
    pub base_url: String,
    pub source_url: String,
    pub started_at: DateTime<Utc>,
    pub completed_at: DateTime<Utc>,
    pub scenarios: Vec<ScenarioReport>,
}

impl SmokeReport {
    pub(crate) fn succeeded(&self) -> bool {
        self.scenarios.iter().all(|scenario| scenario.passed)
    }

    pub(crate) fn print_summary(&self) {
        for scenario in &self.scenarios {
            let status = if scenario.passed { "PASS" } else { "FAIL" };
            println!("{status} {} ({} ms)", scenario.name, scenario.elapsed_ms);
            if let Some(error) = &scenario.error {
                println!("  {error}");
            }
        }
        println!(
            "{} local-staging smoke run {}",
            if self.succeeded() { "PASS" } else { "FAIL" },
            self.run_id
        );
    }
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct ScenarioReport {
    pub name: String,
    pub passed: bool,
    pub elapsed_ms: u128,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(default, skip_serializing_if = "Map::is_empty")]
    pub evidence: Map<String, Value>,
}

impl ScenarioReport {
    pub(crate) fn passed(name: &str, elapsed_ms: u128, evidence: Map<String, Value>) -> Self {
        Self {
            name: name.to_owned(),
            passed: true,
            elapsed_ms,
            error: None,
            evidence,
        }
    }

    pub(crate) fn failed(name: &str, elapsed_ms: u128, error: &anyhow::Error) -> Self {
        Self {
            name: name.to_owned(),
            passed: false,
            elapsed_ms,
            error: Some(format!("{error:#}")),
            evidence: Map::new(),
        }
    }
}
