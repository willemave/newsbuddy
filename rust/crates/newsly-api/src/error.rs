use axum::Json;
use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use newsly_contracts::ErrorEnvelope;
use serde_json::{Map, Value};

#[derive(Debug)]
pub struct ApiError {
    status: StatusCode,
    envelope: Box<ErrorEnvelope>,
    headers: HeaderMap,
}

impl ApiError {
    pub fn new(
        status: StatusCode,
        code: impl Into<String>,
        message: impl Into<String>,
        request_id: impl Into<String>,
    ) -> Self {
        let request_id = request_id.into();
        let mut headers = HeaderMap::new();
        if let Ok(value) = HeaderValue::from_str(&request_id) {
            headers.insert("x-request-id", value);
        }
        Self {
            status,
            envelope: Box::new(ErrorEnvelope {
                code: code.into(),
                message: message.into(),
                details: None,
                retryable: false,
                request_id,
            }),
            headers,
        }
    }

    pub fn with_details(mut self, details: Map<String, Value>) -> Self {
        self.envelope.details = Some(details);
        self
    }

    pub fn with_retryable(mut self, retryable: bool) -> Self {
        self.envelope.retryable = retryable;
        self
    }

    pub fn bearer(mut self) -> Self {
        self.headers
            .insert("www-authenticate", HeaderValue::from_static("Bearer"));
        self
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.status, self.headers, Json(*self.envelope)).into_response()
    }
}
