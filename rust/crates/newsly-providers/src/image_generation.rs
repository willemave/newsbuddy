use std::env;
use std::fmt::{self, Debug, Formatter};
use std::time::Duration;

use base64::Engine;
use futures_util::StreamExt;
use newsly_extraction::PublicUrl;
use reqwest::header::LOCATION;
use reqwest::{Response, StatusCode, Url};
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use thiserror::Error;
use uuid::Uuid;

const DEFAULT_RUNWARE_API_URL: &str = "https://api.runware.ai/v1";
const DEFAULT_GOOGLE_API_URL: &str = "https://generativelanguage.googleapis.com";
const DEFAULT_GOOGLE_VERTEX_API_URL: &str = "https://aiplatform.googleapis.com";
const DEFAULT_RUNWARE_MODEL: &str = "bytedance:seedream@5.0-lite";
const DEFAULT_GOOGLE_IMAGE_MODEL: &str = "gemini-3.1-flash-image-preview";
const SEEDREAM_INFOGRAPHIC_WIDTH: u32 = 2_848;
const SEEDREAM_INFOGRAPHIC_HEIGHT: u32 = 1_600;
const DEFAULT_INFOGRAPHIC_WIDTH: u32 = 1_024;
const DEFAULT_INFOGRAPHIC_HEIGHT: u32 = 576;
const RUNWARE_INLINE_ATTEMPTS: usize = 2;
const RUNWARE_IMAGE_MAX_REDIRECTS: usize = 3;
const DEFAULT_MAX_IMAGE_BYTES: usize = 30_000_000;
const MAX_PROVIDER_ERROR_CHARS: usize = 1_000;

const INFOGRAPHIC_NEGATIVE_PROMPT: &str = "readable text, words, letters, numbers, captions, labels, headlines, logos, watermarks, screenshots, website UI, app interface, chart axes, poster, document page, printed page, magazine spread, dashboard, phone screen, tablet screen, desktop monitor, laptop, computer, office workstation";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InfographicProvider {
    Runware,
    Google,
}

impl InfographicProvider {
    fn parse(value: &str) -> Result<Self, ImageGenerationError> {
        match value.trim().to_ascii_lowercase().as_str() {
            "runware" => Ok(Self::Runware),
            "google" => Ok(Self::Google),
            _ => Err(ImageGenerationError::InvalidConfiguration(
                "INFOGRAPHIC_GENERATION_PROVIDER must be runware or google".to_owned(),
            )),
        }
    }
}

#[derive(Clone)]
pub enum GoogleImageAuth {
    ApiKey(SecretString),
    Bearer {
        access_token: SecretString,
        project: String,
        location: String,
    },
}

impl Debug for GoogleImageAuth {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        match self {
            Self::ApiKey(_) => formatter
                .debug_tuple("ApiKey")
                .field(&"[REDACTED]")
                .finish(),
            Self::Bearer {
                project, location, ..
            } => formatter
                .debug_struct("Bearer")
                .field("access_token", &"[REDACTED]")
                .field("project", project)
                .field("location", location)
                .finish(),
        }
    }
}

#[derive(Clone)]
pub struct ImageGenerationGatewayConfig {
    pub infographic_provider: InfographicProvider,
    pub runware_api_url: Url,
    pub runware_api_key: Option<SecretString>,
    pub runware_models: Vec<String>,
    pub google_api_url: Url,
    pub google_auth: Option<GoogleImageAuth>,
    pub google_models: Vec<String>,
    pub request_timeout: Duration,
    pub max_image_bytes: usize,
}

impl Debug for ImageGenerationGatewayConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ImageGenerationGatewayConfig")
            .field("infographic_provider", &self.infographic_provider)
            .field("runware_api_url", &self.runware_api_url)
            .field(
                "runware_api_key",
                &self.runware_api_key.as_ref().map(|_| "[REDACTED]"),
            )
            .field("runware_models", &self.runware_models)
            .field("google_api_url", &self.google_api_url)
            .field("google_auth", &self.google_auth)
            .field("google_models", &self.google_models)
            .field("request_timeout", &self.request_timeout)
            .field("max_image_bytes", &self.max_image_bytes)
            .finish()
    }
}

impl ImageGenerationGatewayConfig {
    /// Loads and validates the image-provider configuration from the process environment.
    ///
    /// # Errors
    ///
    /// Returns an error when a URL, provider, model list, credential combination, timeout, or
    /// response-size bound is invalid.
    pub fn from_env() -> Result<Self, ImageGenerationError> {
        let infographic_provider = InfographicProvider::parse(
            &env::var("INFOGRAPHIC_GENERATION_PROVIDER").unwrap_or_else(|_| "runware".to_owned()),
        )?;
        let runware_api_url = Url::parse(
            &env::var("RUNWARE_API_URL").unwrap_or_else(|_| DEFAULT_RUNWARE_API_URL.to_owned()),
        )
        .map_err(|error| ImageGenerationError::InvalidConfiguration(error.to_string()))?;
        let runware_api_key = secret_env("RUNWARE_API_KEY");
        let runware_models = resolve_models(
            clean_env("INFOGRAPHIC_GENERATION_MODEL")
                .unwrap_or_else(|| DEFAULT_RUNWARE_MODEL.to_owned()),
            clean_env("INFOGRAPHIC_GENERATION_FALLBACK_MODEL"),
        )?;
        let google_auth = google_auth_from_env()?;
        let google_api_url = Url::parse(
            &clean_env("GOOGLE_IMAGE_API_BASE_URL")
                .unwrap_or_else(|| default_google_api_url(google_auth.as_ref())),
        )
        .map_err(|error| ImageGenerationError::InvalidConfiguration(error.to_string()))?;
        let google_models = resolve_models(
            clean_env("IMAGE_GENERATION_MODEL")
                .unwrap_or_else(|| DEFAULT_GOOGLE_IMAGE_MODEL.to_owned()),
            clean_env("IMAGE_GENERATION_FALLBACK_MODEL"),
        )?;
        let request_timeout =
            Duration::from_secs(env_u64("IMAGE_GENERATION_TIMEOUT_SECONDS", 180)?);
        let max_image_bytes =
            env_usize("IMAGE_GENERATION_MAX_IMAGE_BYTES", DEFAULT_MAX_IMAGE_BYTES)?;
        let config = Self {
            infographic_provider,
            runware_api_url,
            runware_api_key,
            runware_models,
            google_api_url,
            google_auth,
            google_models,
            request_timeout,
            max_image_bytes,
        };
        config.validate()?;
        Ok(config)
    }

    fn validate(&self) -> Result<(), ImageGenerationError> {
        if self.request_timeout.is_zero() || self.request_timeout > Duration::from_secs(300) {
            return Err(ImageGenerationError::InvalidConfiguration(
                "image generation timeout must be between 1 and 300 seconds".to_owned(),
            ));
        }
        if !(1_024..=100_000_000).contains(&self.max_image_bytes) {
            return Err(ImageGenerationError::InvalidConfiguration(
                "image generation byte bound must be between 1024 and 100000000".to_owned(),
            ));
        }
        if self.infographic_provider == InfographicProvider::Runware
            && self.runware_api_key.is_none()
        {
            return Err(ImageGenerationError::InvalidConfiguration(
                "RUNWARE_API_KEY is required when Runware owns infographic generation".to_owned(),
            ));
        }
        if self.infographic_provider == InfographicProvider::Google && self.google_auth.is_none() {
            return Err(ImageGenerationError::InvalidConfiguration(
                "GOOGLE_API_KEY, GEMINI_API_KEY, or GOOGLE_OAUTH_ACCESS_TOKEN is required when Google owns infographic generation".to_owned(),
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct ImageGenerationUsage {
    pub provider: String,
    pub model: String,
    pub request_id: Option<String>,
    pub input_tokens: Option<i64>,
    pub cache_read_tokens: Option<i64>,
    pub output_tokens: Option<i64>,
    pub total_tokens: Option<i64>,
    pub request_count: i64,
    pub response_cost_usd: Option<f64>,
    pub metadata: Value,
}

#[derive(Debug, Clone, PartialEq)]
pub struct GeneratedImage {
    pub bytes: Vec<u8>,
    pub usage: ImageGenerationUsage,
}

#[derive(Debug, Clone)]
pub struct ImageGenerationGateway {
    client: reqwest::Client,
    config: ImageGenerationGatewayConfig,
}

impl ImageGenerationGateway {
    /// Builds a provider gateway from process configuration.
    ///
    /// # Errors
    ///
    /// Returns an error when the image-provider configuration or HTTP client is invalid.
    pub fn from_env() -> Result<Self, ImageGenerationError> {
        Self::new(ImageGenerationGatewayConfig::from_env()?)
    }

    /// Builds a provider gateway from an explicit, validated configuration.
    ///
    /// # Errors
    ///
    /// Returns an error when the configuration or HTTP client is invalid.
    pub fn new(config: ImageGenerationGatewayConfig) -> Result<Self, ImageGenerationError> {
        config.validate()?;
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(15))
            .timeout(config.request_timeout)
            .redirect(reqwest::redirect::Policy::none())
            .build()?;
        Ok(Self { client, config })
    }

    pub const fn max_image_bytes(&self) -> usize {
        self.config.max_image_bytes
    }

    /// Generates one infographic, including configured provider and model fallbacks.
    ///
    /// # Errors
    ///
    /// Returns an error for an invalid prompt, provider failure, malformed response, unsafe image
    /// URL, or response that exceeds the configured byte bound.
    pub async fn generate_infographic(
        &self,
        prompt: &str,
        content_id: i64,
        task_id: i64,
    ) -> Result<GeneratedImage, ImageGenerationError> {
        if prompt.trim().is_empty() {
            return Err(ImageGenerationError::InvalidPrompt);
        }
        match self.config.infographic_provider {
            InfographicProvider::Runware => {
                match self.generate_runware(prompt, content_id, task_id).await {
                    Ok(image) => Ok(image),
                    Err(error) if error.fallback_allowed() && self.config.google_auth.is_some() => {
                        tracing::warn!(
                            content_id,
                            task_id,
                            error = %error,
                            "Runware infographic generation failed; using configured Google fallback"
                        );
                        self.generate_google(prompt, content_id, true).await
                    }
                    Err(error) => Err(error),
                }
            }
            InfographicProvider::Google => self.generate_google(prompt, content_id, false).await,
        }
    }

    async fn generate_runware(
        &self,
        prompt: &str,
        content_id: i64,
        task_id: i64,
    ) -> Result<GeneratedImage, ImageGenerationError> {
        let api_key = self.config.runware_api_key.as_ref().ok_or_else(|| {
            ImageGenerationError::InvalidConfiguration("RUNWARE_API_KEY is missing".to_owned())
        })?;
        let mut last_error = None;
        for model in &self.config.runware_models {
            let options = runware_request_options(model);
            for inline_attempt in 1..=RUNWARE_INLINE_ATTEMPTS {
                let task_uuid = Uuid::new_v4().to_string();
                let request = RunwareRequest {
                    task_type: "imageInference",
                    task_uuid: &task_uuid,
                    include_cost: true,
                    output_type: "URL",
                    output_format: "PNG",
                    positive_prompt: prompt,
                    model,
                    number_results: 1,
                    width: options.width,
                    height: options.height,
                    negative_prompt: options.negative_prompt,
                };
                let result = self
                    .runware_attempt(api_key, &request, content_id, task_id, inline_attempt)
                    .await;
                match result {
                    Ok(image) => return Ok(image),
                    Err(error) => {
                        let retry_inline =
                            error.retryable() && inline_attempt < RUNWARE_INLINE_ATTEMPTS;
                        tracing::warn!(
                            content_id,
                            task_id,
                            model,
                            inline_attempt,
                            retryable = error.retryable(),
                            fallback_allowed = error.fallback_allowed(),
                            error = %error,
                            "Runware image attempt failed"
                        );
                        last_error = Some(error);
                        if !retry_inline {
                            break;
                        }
                    }
                }
            }
        }
        Err(last_error.unwrap_or(ImageGenerationError::NoConfiguredModels))
    }

    async fn runware_attempt(
        &self,
        api_key: &SecretString,
        request: &RunwareRequest<'_>,
        _content_id: i64,
        _task_id: i64,
        inline_attempt: usize,
    ) -> Result<GeneratedImage, ImageGenerationError> {
        let response = self
            .client
            .post(self.config.runware_api_url.clone())
            .bearer_auth(api_key.expose_secret())
            .json(&[request])
            .send()
            .await
            .map_err(ImageGenerationError::RunwareRequest)?;
        let status = response.status();
        let body = read_bounded(response, 2_000_000).await?;
        let payload: RunwareEnvelope = match serde_json::from_slice(&body) {
            Ok(payload) => payload,
            Err(_) if !status.is_success() => {
                return Err(ImageGenerationError::RunwareStatus {
                    status,
                    message: bounded_text(&body, MAX_PROVIDER_ERROR_CHARS),
                    code: None,
                    parameter: None,
                });
            }
            Err(error) => {
                return Err(ImageGenerationError::RunwarePayload(format!(
                    "Runware returned invalid JSON: {error}"
                )));
            }
        };
        if !status.is_success() || !payload.errors.is_empty() {
            return Err(runware_status_error(status, payload.errors.first()));
        }
        let result = payload.data.into_iter().next().ok_or_else(|| {
            ImageGenerationError::RunwarePayload("Runware did not return inference data".to_owned())
        })?;
        let image_url = result
            .image_url
            .or(result.image_url_camel)
            .or(result.image_url_snake)
            .ok_or_else(|| {
                ImageGenerationError::RunwarePayload(
                    "Runware did not return an image URL".to_owned(),
                )
            })?;
        let bytes = self.download_runware_image(&image_url).await?;
        if bytes.is_empty() {
            return Err(ImageGenerationError::EmptyImage);
        }
        let metadata = json!({
            "image_type": "infographic",
            "provider": "runware",
            "response_cost_usd": result.cost,
            "image_url": image_url,
            "task_uuid": request.task_uuid,
            "inline_attempt": inline_attempt,
            "width": request.width,
            "height": request.height,
        });
        Ok(GeneratedImage {
            bytes,
            usage: ImageGenerationUsage {
                provider: "runware".to_owned(),
                model: request.model.to_owned(),
                request_id: Some(request.task_uuid.to_owned()),
                input_tokens: None,
                cache_read_tokens: None,
                output_tokens: None,
                total_tokens: None,
                request_count: 1,
                response_cost_usd: value_as_f64(result.cost.as_ref()),
                metadata,
            },
        })
    }

    async fn download_runware_image(&self, raw_url: &str) -> Result<Vec<u8>, ImageGenerationError> {
        let mut current = public_https_url(raw_url)?;
        for redirect_count in 0..=RUNWARE_IMAGE_MAX_REDIRECTS {
            current
                .validate_dns()
                .await
                .map_err(|_| ImageGenerationError::UnsafeProviderImageUrl)?;
            let response = self
                .client
                .get(current.as_url().clone())
                .send()
                .await
                .map_err(ImageGenerationError::RunwareDownload)?;
            let status = response.status();
            if !status.is_redirection() {
                if !status.is_success() {
                    return Err(ImageGenerationError::RunwareImageStatus(status));
                }
                return read_bounded(response, self.config.max_image_bytes).await;
            }
            if redirect_count == RUNWARE_IMAGE_MAX_REDIRECTS {
                return Err(ImageGenerationError::RunwareImageStatus(status));
            }
            let location = response
                .headers()
                .get(LOCATION)
                .and_then(|value| value.to_str().ok())
                .ok_or(ImageGenerationError::UnsafeProviderImageUrl)?;
            let next = current
                .as_url()
                .join(location)
                .map_err(|_| ImageGenerationError::UnsafeProviderImageUrl)?;
            current = public_https_url(next.as_str())?;
        }
        Err(ImageGenerationError::UnsafeProviderImageUrl)
    }

    async fn generate_google(
        &self,
        prompt: &str,
        content_id: i64,
        fallback_from_runware: bool,
    ) -> Result<GeneratedImage, ImageGenerationError> {
        let auth = self.config.google_auth.as_ref().ok_or_else(|| {
            ImageGenerationError::InvalidConfiguration(
                "Google image credentials are missing".to_owned(),
            )
        })?;
        let mut last_error = None;
        for (index, model) in self.config.google_models.iter().enumerate() {
            let result = self
                .google_attempt(auth, model, prompt, content_id, fallback_from_runware)
                .await;
            match result {
                Ok(image) => return Ok(image),
                Err(error)
                    if index + 1 < self.config.google_models.len() && error.model_unavailable() =>
                {
                    tracing::warn!(
                        content_id,
                        model,
                        fallback_model = %self.config.google_models[index + 1],
                        error = %error,
                        "Google image model unavailable; trying configured fallback"
                    );
                    last_error = Some(error);
                }
                Err(error) => return Err(error),
            }
        }
        Err(last_error.unwrap_or(ImageGenerationError::NoConfiguredModels))
    }

    async fn google_attempt(
        &self,
        auth: &GoogleImageAuth,
        model: &str,
        prompt: &str,
        _content_id: i64,
        fallback_from_runware: bool,
    ) -> Result<GeneratedImage, ImageGenerationError> {
        let endpoint = google_endpoint(&self.config.google_api_url, auth, model)?;
        let body = GoogleGenerateRequest {
            contents: [GoogleContent {
                role: "user",
                parts: [GoogleTextPart { text: prompt }],
            }],
            generation_config: GoogleGenerationConfig {
                response_modalities: ["IMAGE"],
                image_config: GoogleImageConfig {
                    aspect_ratio: "16:9",
                    image_size: "512",
                },
            },
        };
        let mut request = self.client.post(endpoint).json(&body);
        request = match auth {
            GoogleImageAuth::ApiKey(api_key) => {
                request.header("x-goog-api-key", api_key.expose_secret())
            }
            GoogleImageAuth::Bearer { access_token, .. } => {
                request.bearer_auth(access_token.expose_secret())
            }
        };
        let response = request
            .send()
            .await
            .map_err(ImageGenerationError::GoogleRequest)?;
        let status = response.status();
        let response_bound = self
            .config
            .max_image_bytes
            .saturating_mul(4)
            .saturating_div(3)
            .saturating_add(2_000_000);
        let bytes = read_bounded(response, response_bound).await?;
        if !status.is_success() {
            let detail = bounded_text(&bytes, MAX_PROVIDER_ERROR_CHARS);
            return Err(ImageGenerationError::GoogleStatus { status, detail });
        }
        let payload: GoogleGenerateResponse = serde_json::from_slice(&bytes)?;
        let encoded = payload
            .candidates
            .iter()
            .flat_map(|candidate| &candidate.content.parts)
            .find_map(|part| {
                part.inline_data.as_ref().and_then(|data| {
                    data.mime_type
                        .starts_with("image/")
                        .then_some(data.data.as_str())
                })
            })
            .ok_or(ImageGenerationError::NoImageInGoogleResponse)?;
        let image_bytes = base64::engine::general_purpose::STANDARD.decode(encoded)?;
        if image_bytes.is_empty() {
            return Err(ImageGenerationError::EmptyImage);
        }
        if image_bytes.len() > self.config.max_image_bytes {
            return Err(ImageGenerationError::ResponseTooLarge {
                limit: self.config.max_image_bytes,
            });
        }
        let usage = payload.usage_metadata.unwrap_or_default();
        let metadata = json!({
            "image_type": "infographic",
            "image_size": "512",
            "provider": "google",
            "fallback_from_runware": fallback_from_runware,
            "model_version": payload.model_version,
            "reasoning_tokens": usage.thoughts,
        });
        Ok(GeneratedImage {
            bytes: image_bytes,
            usage: ImageGenerationUsage {
                provider: "google".to_owned(),
                model: model.to_owned(),
                request_id: payload.response_id,
                input_tokens: usage.prompt,
                cache_read_tokens: usage.cached_content,
                output_tokens: usage.candidates,
                total_tokens: usage.total,
                request_count: 1,
                response_cost_usd: None,
                metadata,
            },
        })
    }
}

#[derive(Debug, Clone, Copy)]
struct RunwareOptions {
    width: u32,
    height: u32,
    negative_prompt: Option<&'static str>,
}

fn runware_request_options(model: &str) -> RunwareOptions {
    if model == DEFAULT_RUNWARE_MODEL {
        return RunwareOptions {
            width: SEEDREAM_INFOGRAPHIC_WIDTH,
            height: SEEDREAM_INFOGRAPHIC_HEIGHT,
            negative_prompt: None,
        };
    }
    RunwareOptions {
        width: DEFAULT_INFOGRAPHIC_WIDTH,
        height: DEFAULT_INFOGRAPHIC_HEIGHT,
        negative_prompt: Some(INFOGRAPHIC_NEGATIVE_PROMPT),
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct RunwareRequest<'a> {
    task_type: &'static str,
    #[serde(rename = "taskUUID")]
    task_uuid: &'a str,
    include_cost: bool,
    output_type: &'static str,
    output_format: &'static str,
    positive_prompt: &'a str,
    model: &'a str,
    number_results: u8,
    width: u32,
    height: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    negative_prompt: Option<&'static str>,
}

#[derive(Debug, Deserialize)]
struct RunwareEnvelope {
    #[serde(default)]
    data: Vec<RunwareResult>,
    #[serde(default)]
    errors: Vec<RunwareErrorPayload>,
}

#[derive(Debug, Deserialize)]
struct RunwareResult {
    #[serde(rename = "imageURL")]
    image_url: Option<String>,
    #[serde(rename = "imageUrl")]
    image_url_camel: Option<String>,
    #[serde(rename = "image_url")]
    image_url_snake: Option<String>,
    cost: Option<Value>,
}

#[derive(Debug, Deserialize)]
struct RunwareErrorPayload {
    message: Option<String>,
    code: Option<Value>,
    parameter: Option<String>,
}

fn runware_status_error(
    status: StatusCode,
    payload: Option<&RunwareErrorPayload>,
) -> ImageGenerationError {
    let message = payload
        .and_then(|value| value.message.as_deref())
        .unwrap_or("Runware request failed")
        .chars()
        .take(MAX_PROVIDER_ERROR_CHARS)
        .collect();
    let parameter = payload.and_then(|value| value.parameter.clone());
    let code = payload.and_then(|value| value.code.as_ref()).map(|value| {
        value
            .as_str()
            .map_or_else(|| value.to_string(), str::to_owned)
    });
    ImageGenerationError::RunwareStatus {
        status,
        message,
        code,
        parameter,
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct GoogleGenerateRequest<'a> {
    contents: [GoogleContent<'a>; 1],
    generation_config: GoogleGenerationConfig<'a>,
}

#[derive(Debug, Serialize)]
struct GoogleContent<'a> {
    role: &'static str,
    parts: [GoogleTextPart<'a>; 1],
}

#[derive(Debug, Serialize)]
struct GoogleTextPart<'a> {
    text: &'a str,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct GoogleGenerationConfig<'a> {
    response_modalities: [&'static str; 1],
    image_config: GoogleImageConfig<'a>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct GoogleImageConfig<'a> {
    aspect_ratio: &'static str,
    image_size: &'a str,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct GoogleGenerateResponse {
    #[serde(default)]
    candidates: Vec<GoogleCandidate>,
    usage_metadata: Option<GoogleUsageMetadata>,
    response_id: Option<String>,
    model_version: Option<String>,
}

#[derive(Debug, Deserialize)]
struct GoogleCandidate {
    content: GoogleResponseContent,
}

#[derive(Debug, Deserialize)]
struct GoogleResponseContent {
    #[serde(default)]
    parts: Vec<GoogleResponsePart>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct GoogleResponsePart {
    inline_data: Option<GoogleInlineData>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct GoogleInlineData {
    mime_type: String,
    data: String,
}

#[derive(Debug, Default, Deserialize)]
struct GoogleUsageMetadata {
    #[serde(rename = "promptTokenCount")]
    prompt: Option<i64>,
    #[serde(rename = "cachedContentTokenCount")]
    cached_content: Option<i64>,
    #[serde(rename = "candidatesTokenCount")]
    candidates: Option<i64>,
    #[serde(rename = "totalTokenCount")]
    total: Option<i64>,
    #[serde(rename = "thoughtsTokenCount")]
    thoughts: Option<i64>,
}

fn google_endpoint(
    base: &Url,
    auth: &GoogleImageAuth,
    model: &str,
) -> Result<Url, ImageGenerationError> {
    let mut endpoint = base.clone();
    {
        let mut segments = endpoint.path_segments_mut().map_err(|()| {
            ImageGenerationError::InvalidConfiguration(
                "Google image API URL cannot be used as a base URL".to_owned(),
            )
        })?;
        segments.pop_if_empty();
        match auth {
            GoogleImageAuth::ApiKey(_) => {
                segments.extend(["v1beta", "models"]);
                segments.push(&format!("{model}:generateContent"));
            }
            GoogleImageAuth::Bearer {
                project, location, ..
            } => {
                segments.extend(["v1", "projects", project, "locations", location]);
                segments.extend(["publishers", "google", "models"]);
                segments.push(&format!("{model}:generateContent"));
            }
        }
    }
    Ok(endpoint)
}

fn public_https_url(raw_url: &str) -> Result<PublicUrl, ImageGenerationError> {
    let url =
        PublicUrl::parse(raw_url).map_err(|_| ImageGenerationError::UnsafeProviderImageUrl)?;
    if url.as_url().scheme() != "https" {
        return Err(ImageGenerationError::UnsafeProviderImageUrl);
    }
    Ok(url)
}

async fn read_bounded(response: Response, limit: usize) -> Result<Vec<u8>, ImageGenerationError> {
    if response
        .content_length()
        .is_some_and(|length| length > u64::try_from(limit).unwrap_or(u64::MAX))
    {
        return Err(ImageGenerationError::ResponseTooLarge { limit });
    }
    let mut bytes = Vec::new();
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk?;
        if bytes.len().saturating_add(chunk.len()) > limit {
            return Err(ImageGenerationError::ResponseTooLarge { limit });
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}

fn google_auth_from_env() -> Result<Option<GoogleImageAuth>, ImageGenerationError> {
    if let Some(access_token) = secret_env("GOOGLE_OAUTH_ACCESS_TOKEN") {
        let project = clean_env("GOOGLE_CLOUD_PROJECT").ok_or_else(|| {
            ImageGenerationError::InvalidConfiguration(
                "GOOGLE_CLOUD_PROJECT is required with GOOGLE_OAUTH_ACCESS_TOKEN".to_owned(),
            )
        })?;
        let location = clean_env("GOOGLE_CLOUD_LOCATION").unwrap_or_else(|| "global".to_owned());
        return Ok(Some(GoogleImageAuth::Bearer {
            access_token,
            project,
            location,
        }));
    }
    Ok(secret_env("GOOGLE_API_KEY")
        .or_else(|| secret_env("GEMINI_API_KEY"))
        .map(GoogleImageAuth::ApiKey))
}

fn default_google_api_url(auth: Option<&GoogleImageAuth>) -> String {
    let Some(GoogleImageAuth::Bearer { location, .. }) = auth else {
        return DEFAULT_GOOGLE_API_URL.to_owned();
    };
    if location == "global" {
        DEFAULT_GOOGLE_VERTEX_API_URL.to_owned()
    } else {
        format!("https://{location}-aiplatform.googleapis.com")
    }
}

fn resolve_models(
    primary: String,
    fallback: Option<String>,
) -> Result<Vec<String>, ImageGenerationError> {
    let mut models = Vec::new();
    for model in [Some(primary), fallback].into_iter().flatten() {
        let normalized = model.trim();
        if !normalized.is_empty() && !models.iter().any(|existing| existing == normalized) {
            models.push(normalized.to_owned());
        }
    }
    if models.is_empty() {
        return Err(ImageGenerationError::NoConfiguredModels);
    }
    Ok(models)
}

fn clean_env(name: &str) -> Option<String> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn secret_env(name: &str) -> Option<SecretString> {
    clean_env(name).map(SecretString::from)
}

fn env_u64(name: &str, default: u64) -> Result<u64, ImageGenerationError> {
    clean_env(name).map_or(Ok(default), |value| {
        value.parse::<u64>().map_err(|_| {
            ImageGenerationError::InvalidConfiguration(format!(
                "{name} must be an unsigned integer"
            ))
        })
    })
}

fn env_usize(name: &str, default: usize) -> Result<usize, ImageGenerationError> {
    clean_env(name).map_or(Ok(default), |value| {
        value.parse::<usize>().map_err(|_| {
            ImageGenerationError::InvalidConfiguration(format!(
                "{name} must be an unsigned integer"
            ))
        })
    })
}

fn value_as_f64(value: Option<&Value>) -> Option<f64> {
    match value {
        Some(Value::Number(number)) => number.as_f64(),
        Some(Value::String(value)) => value.parse().ok(),
        _ => None,
    }
}

fn bounded_text(bytes: &[u8], max_chars: usize) -> String {
    String::from_utf8_lossy(bytes)
        .chars()
        .take(max_chars)
        .collect()
}

#[derive(Debug, Error)]
pub enum ImageGenerationError {
    #[error("invalid image-generation configuration: {0}")]
    InvalidConfiguration(String),
    #[error("image-generation prompt is empty")]
    InvalidPrompt,
    #[error("no image-generation models are configured")]
    NoConfiguredModels,
    #[error("Runware request failed")]
    RunwareRequest(#[source] reqwest::Error),
    #[error("Runware image download failed")]
    RunwareDownload(#[source] reqwest::Error),
    #[error("Runware returned HTTP {status}: {message}")]
    RunwareStatus {
        status: StatusCode,
        message: String,
        code: Option<String>,
        parameter: Option<String>,
    },
    #[error("Runware returned an invalid payload: {0}")]
    RunwarePayload(String),
    #[error("Runware image download returned HTTP {0}")]
    RunwareImageStatus(StatusCode),
    #[error("provider image URL must be a valid HTTPS URL")]
    UnsafeProviderImageUrl,
    #[error("Google image request failed")]
    GoogleRequest(#[source] reqwest::Error),
    #[error("Google image generation returned HTTP {status}: {detail}")]
    GoogleStatus { status: StatusCode, detail: String },
    #[error("Google response did not contain an image")]
    NoImageInGoogleResponse,
    #[error("image provider returned an empty image")]
    EmptyImage,
    #[error("image provider response exceeded the {limit}-byte bound")]
    ResponseTooLarge { limit: usize },
    #[error("image provider returned invalid JSON")]
    Json(#[from] serde_json::Error),
    #[error("image provider returned invalid base64")]
    Base64(#[from] base64::DecodeError),
    #[error("image provider HTTP client failed")]
    Http(#[from] reqwest::Error),
}

impl ImageGenerationError {
    pub fn retryable(&self) -> bool {
        match self {
            Self::RunwareRequest(error)
            | Self::RunwareDownload(error)
            | Self::GoogleRequest(error)
            | Self::Http(error) => error.is_timeout() || error.is_connect() || error.is_request(),
            Self::RunwareStatus {
                status,
                parameter,
                message,
                ..
            } => {
                status.is_server_error()
                    || *status == StatusCode::TOO_MANY_REQUESTS
                    || parameter.as_deref() == Some("taskUUID")
                    || message.to_ascii_lowercase().contains("taskuuid")
            }
            Self::RunwareImageStatus(status) | Self::GoogleStatus { status, .. } => {
                status.is_server_error() || *status == StatusCode::TOO_MANY_REQUESTS
            }
            Self::ResponseTooLarge { .. }
            | Self::InvalidConfiguration(_)
            | Self::InvalidPrompt
            | Self::NoConfiguredModels
            | Self::UnsafeProviderImageUrl
            | Self::NoImageInGoogleResponse
            | Self::EmptyImage
            | Self::Json(_)
            | Self::Base64(_)
            | Self::RunwarePayload(_) => false,
        }
    }

    pub fn fallback_allowed(&self) -> bool {
        match self {
            Self::RunwareRequest(_)
            | Self::RunwareDownload(_)
            | Self::RunwareImageStatus(_)
            | Self::RunwarePayload(_)
            | Self::Http(_) => true,
            Self::RunwareStatus {
                status, parameter, ..
            } => {
                status.is_client_error()
                    || status.is_server_error()
                    || parameter.as_deref() == Some("taskUUID")
            }
            _ => false,
        }
    }

    fn model_unavailable(&self) -> bool {
        matches!(
            self,
            Self::GoogleStatus {
                status: StatusCode::NOT_FOUND,
                ..
            }
        )
    }
}

#[cfg(test)]
mod tests;
