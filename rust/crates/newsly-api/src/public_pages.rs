use std::env;
use std::path::{Component, Path as FilePath, PathBuf};

use axum::Router;
use axum::body::{Body, Bytes};
use axum::extract::Path;
use axum::http::header::{
    CACHE_CONTROL, CONTENT_TYPE, HeaderName, HeaderValue, REFERRER_POLICY, X_CONTENT_TYPE_OPTIONS,
};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{Html, IntoResponse, Response};
use axum::routing::get;

use crate::{AppState, request_id_from_headers};

const PRIVATE_CACHE_CONTROL: HeaderValue = HeaderValue::from_static("no-store");
const NO_REFERRER: HeaderValue = HeaderValue::from_static("no-referrer");
const NO_SNIFF: HeaderValue = HeaderValue::from_static("nosniff");
const NO_INDEX: HeaderValue = HeaderValue::from_static("noindex, nofollow, noarchive");

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/", get(home))
        .route("/privacy", get(privacy))
        .route("/support", get(support))
        .route("/terms", get(terms))
        .route("/robots.txt", get(robots))
        .route("/static/images/{*asset_path}", get(serve_image))
        .route("/admin/static/{*asset_path}", get(serve_admin_asset))
}

async fn home() -> Response {
    private_html(PUBLIC_HOME_HTML)
}

async fn privacy() -> Response {
    private_html(PRIVACY_HTML)
}

async fn support() -> Response {
    private_html(SUPPORT_HTML)
}

async fn terms() -> Response {
    private_html(TERMS_HTML)
}

async fn robots() -> Response {
    let mut response = "User-agent: *\nDisallow: /\n".into_response();
    response.headers_mut().insert(
        CONTENT_TYPE,
        HeaderValue::from_static("text/plain; charset=utf-8"),
    );
    add_private_headers(response.headers_mut());
    response
}

async fn serve_image(headers: HeaderMap, Path(asset_path): Path<String>) -> Response {
    let configured =
        env::var_os("IMAGES_BASE_DIR").map_or_else(|| PathBuf::from("data/images"), PathBuf::from);
    serve_bounded_file(
        configured,
        &asset_path,
        true,
        request_id_from_headers(&headers),
    )
    .await
}

async fn serve_admin_asset(headers: HeaderMap, Path(asset_path): Path<String>) -> Response {
    let root = env::var_os("ADMIN_STATIC_DIR")
        .map_or_else(|| PathBuf::from("rust/assets/admin-static"), PathBuf::from);
    serve_bounded_file(root, &asset_path, false, request_id_from_headers(&headers)).await
}

async fn serve_bounded_file(
    configured_root: PathBuf,
    asset_path: &str,
    immutable_when_versioned: bool,
    request_id: String,
) -> Response {
    const MAX_STATIC_FILE_BYTES: u64 = 32 * 1_024 * 1_024;

    let relative = FilePath::new(asset_path);
    if asset_path.is_empty()
        || asset_path.len() > 2_048
        || asset_path.contains(['\0', '\\'])
        || relative.is_absolute()
        || relative
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return not_found(request_id);
    }
    let root = if configured_root.is_absolute() {
        configured_root
    } else {
        match env::current_dir() {
            Ok(current) => current.join(configured_root),
            Err(error) => return file_error(&error, request_id),
        }
    };
    let canonical_root = match tokio::fs::canonicalize(&root).await {
        Ok(path) => path,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return not_found(request_id),
        Err(error) => return file_error(&error, request_id),
    };
    let requested = root.join(relative);
    let canonical_path = match tokio::fs::canonicalize(&requested).await {
        Ok(path) => path,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return not_found(request_id),
        Err(error) => return file_error(&error, request_id),
    };
    if !canonical_path.starts_with(&canonical_root) {
        return not_found(request_id);
    }
    let metadata = match tokio::fs::metadata(&canonical_path).await {
        Ok(metadata) if metadata.is_file() && metadata.len() <= MAX_STATIC_FILE_BYTES => metadata,
        Ok(_) => return not_found(request_id),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return not_found(request_id),
        Err(error) => return file_error(&error, request_id),
    };
    let bytes = match tokio::fs::read(&canonical_path).await {
        Ok(bytes) if u64::try_from(bytes.len()).ok() == Some(metadata.len()) => bytes,
        Ok(_) => {
            return file_error(
                &std::io::Error::other("static file changed while it was being read"),
                request_id,
            );
        }
        Err(error) => return file_error(&error, request_id),
    };
    let mut response = Response::new(Body::from(Bytes::from(bytes)));
    response.headers_mut().insert(
        CONTENT_TYPE,
        HeaderValue::from_static(content_type_for(&canonical_path)),
    );
    response.headers_mut().insert(
        CACHE_CONTROL,
        if immutable_when_versioned {
            HeaderValue::from_static("public, max-age=86400")
        } else {
            HeaderValue::from_static("public, max-age=3600")
        },
    );
    response
}

fn private_html(content: &'static str) -> Response {
    let mut response = Html(content).into_response();
    add_private_headers(response.headers_mut());
    response
}

fn add_private_headers(headers: &mut HeaderMap) {
    headers.insert(CACHE_CONTROL, PRIVATE_CACHE_CONTROL);
    headers.insert(REFERRER_POLICY, NO_REFERRER);
    headers.insert(X_CONTENT_TYPE_OPTIONS, NO_SNIFF);
    headers.insert(HeaderName::from_static("x-robots-tag"), NO_INDEX);
}

fn not_found(request_id: String) -> Response {
    crate::error::ApiError::new(
        StatusCode::NOT_FOUND,
        "not_found",
        "Static asset not found",
        request_id,
    )
    .into_response()
}

fn file_error(error: &std::io::Error, request_id: String) -> Response {
    tracing::error!(error = %error, "static asset read failed");
    crate::error::ApiError::new(
        StatusCode::INTERNAL_SERVER_ERROR,
        "internal_error",
        "Static asset could not be read",
        request_id,
    )
    .into_response()
}

fn content_type_for(path: &FilePath) -> &'static str {
    match path
        .extension()
        .and_then(|value| value.to_str())
        .map(str::to_ascii_lowercase)
        .as_deref()
    {
        Some("css") => "text/css; charset=utf-8",
        Some("js") => "text/javascript; charset=utf-8",
        Some("html") => "text/html; charset=utf-8",
        Some("json") => "application/json",
        Some("svg") => "image/svg+xml",
        Some("png") => "image/png",
        Some("jpg" | "jpeg") => "image/jpeg",
        Some("webp") => "image/webp",
        Some("gif") => "image/gif",
        Some("ico") => "image/x-icon",
        Some("woff") => "font/woff",
        Some("woff2") => "font/woff2",
        _ => "application/octet-stream",
    }
}

const PUBLIC_HOME_HTML: &str = r#"<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive"><title>Newsbuddy</title>
<style>:root{color-scheme:light;font-family:system-ui,sans-serif}body{margin:0;background:#f7f5f1;color:#24221f}main{max-width:38rem;margin:18vh auto;padding:2rem}h1{margin:0 0 .75rem;font-size:2.25rem}p{color:#5d5952;line-height:1.6}a{color:#8a4b2b}</style></head>
<body><main><h1>Newsbuddy</h1><p>A private news reading and learning service.</p>
<p><a href="/privacy">Privacy</a> · <a href="/support">Support</a> · <a href="/terms">Terms</a> · <a href="/health">Service status</a></p></main></body></html>"#;

const PRIVACY_HTML: &str = concat!(
    r#"<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Newsbuddy Privacy Policy</title>"#,
    r#"<style>:root{color-scheme:light;font-family:system-ui,sans-serif}body{margin:0;background:#f7f5f1;color:#24221f}main{max-width:46rem;margin:4rem auto;padding:0 1.5rem 4rem}h1,h2{line-height:1.2}h2{margin-top:2rem}p,li{color:#4f4b46;line-height:1.65}a{color:#7b4328}nav{margin-bottom:2rem}</style>"#,
    r#"</head><body><main><nav><a href="/">Newsbuddy</a></nav><h1>Privacy Policy</h1><p>Effective August 1, 2026.</p>
<p>Newsbuddy is a personal news reading and learning service. This policy explains the data the service processes to provide the app.</p>
<h2>Data we process</h2><ul><li>Apple account identifiers, name, and email supplied through Sign in with Apple.</li><li>Articles, links, feeds, X bookmarks, prompts, chats, voice transcripts, preferences, and feedback you submit or choose to synchronize.</li><li>Generated summaries, briefings, learning materials, images, and audio.</li><li>Operational records such as request identifiers, error details, task status, and provider usage needed to operate and secure the service.</li></ul>
<h2>External processing</h2><p>To provide requested features, Newsbuddy may send relevant content and instructions to service providers for artificial-intelligence processing, search and retrieval, transcription, speech, image generation, web extraction, hosting, and error monitoring. Providers currently used by configured features can include OpenAI, Anthropic, Google, OpenRouter, ElevenLabs, Exa, E2B, Firecrawl, Runware, Sentry, Cloudflare, and X. Only data needed for the requested operation is sent.</p>
<h2>X synchronization</h2><p>If you connect X, Newsbuddy stores encrypted OAuth credentials on its server and periodically imports your bookmarks in the background. You can disconnect X in Settings. Disconnecting stops future synchronization and revokes the connection; deleting your Newsbuddy account also removes the connection and associated credentials.</p>
<h2>Retention and deletion</h2><p>Data is retained while your account is active and as needed to operate requested features. You can delete your account in the app. Deletion deactivates access, revokes connected services, cancels pending work, and removes account-linked records and files, subject to short-lived backups and legal obligations.</p>
<h2>Your choices</h2><p>You control whether to connect X, submit voice recordings, or use features that send content to external processors. You may disconnect X or delete your account from Settings.</p><h2>Contact</h2><p>Questions may be sent to <a href="mailto:willem.ave@gmail.com">willem.ave@gmail.com</a>.</p></main></body></html>"#
);

const SUPPORT_HTML: &str = concat!(
    r#"<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Newsbuddy Support</title>"#,
    r#"<style>:root{color-scheme:light;font-family:system-ui,sans-serif}body{margin:0;background:#f7f5f1;color:#24221f}main{max-width:46rem;margin:4rem auto;padding:0 1.5rem 4rem}h1,h2{line-height:1.2}h2{margin-top:2rem}p,li{color:#4f4b46;line-height:1.65}a{color:#7b4328}nav{margin-bottom:2rem}</style>"#,
    r#"</head><body><main><nav><a href="/">Newsbuddy</a></nav><h1>Support</h1><p>For help with Newsbuddy, email <a href="mailto:willem.ave@gmail.com">willem.ave@gmail.com</a>.</p><h2>Account and integrations</h2><p>Sign in with Apple is required. X can be connected or disconnected from Settings. Account deletion is available in Settings under Account.</p><h2>Processing time</h2><p>New articles, bookmarks, briefings, and learning materials may take several minutes to prepare. The app shows their processing state while work is underway.</p></main></body></html>"#
);

const TERMS_HTML: &str = concat!(
    r#"<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Newsbuddy Terms</title>"#,
    r#"<style>:root{color-scheme:light;font-family:system-ui,sans-serif}body{margin:0;background:#f7f5f1;color:#24221f}main{max-width:46rem;margin:4rem auto;padding:0 1.5rem 4rem}h1,h2{line-height:1.2}h2{margin-top:2rem}p,li{color:#4f4b46;line-height:1.65}a{color:#7b4328}nav{margin-bottom:2rem}</style>"#,
    r#"</head><body><main><nav><a href="/">Newsbuddy</a></nav><h1>Terms of Use</h1><p>Effective August 1, 2026.</p><p>Newsbuddy provides personal tools for collecting, summarizing, and learning from content you choose. You remain responsible for the links, feeds, accounts, and instructions you submit and for complying with applicable laws and third-party terms.</p><p>Generated material may be incomplete or inaccurate and should not be relied on as professional advice. The service may change or be unavailable, and abusive or unlawful use may result in account suspension.</p><p>You may stop using the service and delete your account at any time from Settings. Questions may be sent to <a href="mailto:willem.ave@gmail.com">willem.ave@gmail.com</a>.</p></main></body></html>"#
);

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn content_type_mapping_is_bounded_and_explicit() {
        assert_eq!(
            content_type_for(FilePath::new("app.css")),
            "text/css; charset=utf-8"
        );
        assert_eq!(content_type_for(FilePath::new("image.png")), "image/png");
        assert_eq!(
            content_type_for(FilePath::new("unknown.bin")),
            "application/octet-stream"
        );
    }
}
