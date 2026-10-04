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

    pub fn validation(message: impl Into<String>, request_id: impl Into<String>) -> Self {
        Self::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            "Request validation failed",
            request_id,
        )
        .with_details(
            serde_json::json!({"errors": [{"message": message.into()}]})
                .as_object()
                .expect("validation details are an object")
                .clone(),
        )
    }

    pub fn validation_message(message: impl Into<String>, request_id: impl Into<String>) -> Self {
        Self::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            message,
            request_id,
        )
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

#[cfg(test)]
mod tests {
    use axum::body::to_bytes;
    use axum::response::IntoResponse;
    use serde_json::{Value, json};

    use super::ApiError;

    async fn body(error: ApiError) -> Value {
        let response = error.into_response();
        let bytes = to_bytes(response.into_body(), 16 * 1024).await.unwrap();
        serde_json::from_slice(&bytes).unwrap()
    }

    #[tokio::test]
    async fn standard_validation_shape_is_stable() {
        assert_eq!(
            body(ApiError::validation("invalid value", "request-1")).await,
            json!({
                "code": "validation_error",
                "message": "Request validation failed",
                "details": {"errors": [{"message": "invalid value"}]},
                "retryable": false,
                "request_id": "request-1"
            })
        );
    }

    #[tokio::test]
    async fn message_validation_shape_is_stable() {
        assert_eq!(
            body(ApiError::validation_message("invalid value", "request-2")).await,
            json!({
                "code": "validation_error",
                "message": "invalid value",
                "details": null,
                "retryable": false,
                "request_id": "request-2"
            })
        );
    }
}
