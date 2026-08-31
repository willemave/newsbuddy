use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum HealthStatus {
    Healthy,
    Unhealthy,
    NotChecked,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct HealthChecks {
    pub process: HealthStatus,
    pub database: HealthStatus,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct HealthResponse {
    pub status: HealthStatus,
    pub service: String,
    pub checks: HealthChecks,
}

impl HealthResponse {
    pub fn live(service: impl Into<String>) -> Self {
        Self {
            status: HealthStatus::Healthy,
            service: service.into(),
            checks: HealthChecks {
                process: HealthStatus::Healthy,
                database: HealthStatus::NotChecked,
            },
        }
    }

    pub fn ready(service: impl Into<String>) -> Self {
        Self {
            status: HealthStatus::Healthy,
            service: service.into(),
            checks: HealthChecks {
                process: HealthStatus::Healthy,
                database: HealthStatus::Healthy,
            },
        }
    }

    pub fn database_unavailable(service: impl Into<String>) -> Self {
        Self {
            status: HealthStatus::Unhealthy,
            service: service.into(),
            checks: HealthChecks {
                process: HealthStatus::Healthy,
                database: HealthStatus::Unhealthy,
            },
        }
    }
}
