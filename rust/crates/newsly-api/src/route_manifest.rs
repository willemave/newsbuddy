use std::cmp::Reverse;

use axum::http::Method;
use serde::Deserialize;
use thiserror::Error;

const EMBEDDED_POLICY_MANIFEST: &str = include_str!("../../../../contracts/policy-manifest.toml");
const SUPPORTED_MANIFEST_VERSION: u32 = 2;

#[derive(Debug, Clone)]
pub(crate) struct RouteManifest {
    routes: Vec<RoutePolicy>,
}

impl RouteManifest {
    /// Parses the checked-in policy manifest embedded in the exact API binary.
    ///
    /// # Errors
    ///
    /// Returns an error when the manifest version or a route declaration is invalid.
    pub(crate) fn embedded() -> Result<Self, RouteManifestError> {
        let manifest: PolicyManifest = toml::from_str(EMBEDDED_POLICY_MANIFEST)?;
        if manifest.version != SUPPORTED_MANIFEST_VERSION {
            return Err(RouteManifestError::UnsupportedVersion(manifest.version));
        }
        if manifest.runtime_registry != "runtime_ownership" {
            return Err(RouteManifestError::InvalidRegistry(
                manifest.runtime_registry,
            ));
        }

        let mut routes = manifest.routes;
        for route in &routes {
            route.validate()?;
        }
        // Prefer literal paths over templates so `/api/content/search` cannot be swallowed by
        // `/api/content/{content_id}`. A stable secondary key makes matching deterministic.
        routes.sort_by_key(|route| {
            (
                Reverse(route.literal_segment_count()),
                Reverse(route.path.len()),
                route.method.clone(),
                route.path.clone(),
            )
        });
        Ok(Self { routes })
    }

    pub(crate) fn find(&self, method: &Method, path: &str) -> Option<&RoutePolicy> {
        self.routes.iter().find(|route| route.matches(method, path))
    }
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct RoutePolicy {
    method: String,
    path: String,
    operation_id: String,
    #[serde(default)]
    write_semantics: bool,
}

impl RoutePolicy {
    pub(crate) fn operation_id(&self) -> &str {
        &self.operation_id
    }

    pub(crate) const fn write_semantics(&self) -> bool {
        self.write_semantics
    }

    fn validate(&self) -> Result<(), RouteManifestError> {
        self.method
            .parse::<Method>()
            .map_err(|_| RouteManifestError::InvalidMethod(self.method.clone()))?;
        if !self.path.starts_with('/') || self.operation_id.trim().is_empty() {
            return Err(RouteManifestError::InvalidRoute {
                method: self.method.clone(),
                path: self.path.clone(),
                operation_id: self.operation_id.clone(),
            });
        }
        Ok(())
    }

    fn matches(&self, method: &Method, path: &str) -> bool {
        if self.method != method.as_str() {
            return false;
        }
        let template_segments = path_segments(&self.path);
        let request_segments = path_segments(path);
        template_segments.len() == request_segments.len()
            && template_segments
                .iter()
                .zip(request_segments)
                .all(|(template, actual)| {
                    (template.starts_with('{') && template.ends_with('}')) || *template == actual
                })
    }

    fn literal_segment_count(&self) -> usize {
        path_segments(&self.path)
            .into_iter()
            .filter(|segment| !(segment.starts_with('{') && segment.ends_with('}')))
            .count()
    }
}

fn path_segments(path: &str) -> Vec<&str> {
    path.trim_end_matches('/')
        .split('/')
        .filter(|segment| !segment.is_empty())
        .collect()
}

#[derive(Debug, Deserialize)]
struct PolicyManifest {
    version: u32,
    runtime_registry: String,
    routes: Vec<RoutePolicy>,
}

#[derive(Debug, Error)]
pub(crate) enum RouteManifestError {
    #[error("ownership policy manifest could not be parsed")]
    Parse(#[from] toml::de::Error),
    #[error("unsupported ownership policy manifest version {0}")]
    UnsupportedVersion(u32),
    #[error("ownership policy manifest uses unexpected registry {0:?}")]
    InvalidRegistry(String),
    #[error("ownership policy route uses invalid HTTP method {0:?}")]
    InvalidMethod(String),
    #[error("invalid ownership policy route {method} {path} ({operation_id})")]
    InvalidRoute {
        method: String,
        path: String,
        operation_id: String,
    },
}

#[cfg(test)]
mod tests {
    use std::collections::{HashMap, HashSet};

    use axum::http::Method;
    use matchit::Router;

    use super::{RouteManifest, RoutePolicy};

    #[test]
    fn embedded_manifest_matches_literal_and_parameterized_routes() {
        let manifest = RouteManifest::embedded().unwrap();
        assert_eq!(
            manifest
                .find(&Method::GET, "/api/jobs/42")
                .unwrap()
                .operation_id(),
            "getJob"
        );
        assert!(manifest.find(&Method::POST, "/api/jobs/42").is_none());
    }

    #[test]
    fn literal_routes_win_over_parameter_templates() {
        let manifest = RouteManifest::embedded().unwrap();
        assert_eq!(
            manifest
                .find(&Method::GET, "/api/content/search")
                .unwrap()
                .operation_id(),
            "searchContents"
        );
    }

    #[test]
    fn embedded_manifest_matches_axum_openapi_routes() {
        const METHODS: &[&str] = &[
            "delete", "get", "head", "options", "patch", "post", "put", "trace",
        ];
        const UNFENCED_HEALTH: &[(&str, &str)] =
            &[("GET", "/health/live"), ("GET", "/health/ready")];

        let manifest = RouteManifest::embedded().unwrap();
        let document = serde_json::to_value(crate::openapi_document()).unwrap();
        let paths = document["paths"].as_object().unwrap();
        let mut openapi_routes = HashSet::new();
        let mut matchers: HashMap<String, Router<String>> = HashMap::new();

        for (path, path_item) in paths {
            for method in METHODS {
                let Some(operation) = path_item.get(*method) else {
                    continue;
                };
                let operation_id = operation["operationId"].as_str().unwrap().to_owned();
                let method = method.to_ascii_uppercase();
                openapi_routes.insert((method.clone(), path.clone(), operation_id.clone()));
                matchers
                    .entry(method)
                    .or_default()
                    .insert(path, operation_id)
                    .unwrap();
            }
        }

        let manifest_routes = manifest
            .routes
            .iter()
            .map(|route| {
                (
                    route.method.clone(),
                    route.path.clone(),
                    route.operation_id.clone(),
                )
            })
            .collect::<HashSet<_>>();
        let expected = openapi_routes
            .iter()
            .filter(|(method, path, _)| {
                !UNFENCED_HEALTH.contains(&(method.as_str(), path.as_str()))
            })
            .cloned()
            .collect::<HashSet<_>>();
        assert_eq!(manifest_routes, expected);

        for route in &manifest.routes {
            let concrete = concrete_path(&route.path);
            let matched = matchers[&route.method].at(&concrete).unwrap();
            assert_eq!(matched.value, &route.operation_id, "{concrete}");
            let method = route.method.parse::<Method>().unwrap();
            assert_eq!(
                manifest
                    .find(&method, &concrete)
                    .map(RoutePolicy::operation_id),
                Some(matched.value.as_str()),
                "{concrete}"
            );
        }

        assert_eq!(
            manifest
                .find(&Method::GET, "/api/content/search")
                .map(RoutePolicy::operation_id),
            Some("searchContents")
        );
        assert_eq!(
            manifest
                .find(&Method::GET, "/api/scrapers")
                .map(RoutePolicy::operation_id),
            Some("listScraperConfigs")
        );
        assert_eq!(
            manifest
                .find(&Method::GET, "/admin/")
                .map(RoutePolicy::operation_id),
            Some("adminDashboard")
        );
        assert!(manifest.find(&Method::GET, "/health/live").is_none());
        assert!(manifest.find(&Method::GET, "/health/ready").is_none());
        assert!(manifest.find(&Method::GET, "/api/unknown").is_none());
        assert!(
            manifest
                .find(&Method::POST, "/api/content/search")
                .is_none()
        );
    }

    fn concrete_path(template: &str) -> String {
        template
            .split('/')
            .map(|segment| {
                if segment.starts_with('{') && segment.ends_with('}') {
                    "123"
                } else {
                    segment
                }
            })
            .collect::<Vec<_>>()
            .join("/")
    }
}
