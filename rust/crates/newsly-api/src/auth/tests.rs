use axum::http::StatusCode;
use newsly_db::AuthenticationRepositoryError;

use super::{AppleIdentityError, api_key_lookup_rejection, bearer_token, verify_apple_nonce};

#[tokio::test]
async fn unknown_apple_key_ids_refresh_the_key_set_once_per_interval() {
    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::time::Duration;

    use secrecy::SecretString;

    use super::{AppleIdentityError, AuthConfig, AuthConfigInput};

    let fetches = Arc::new(AtomicUsize::new(0));
    let counter = Arc::clone(&fetches);
    let app = axum::Router::new().route(
        "/keys",
        axum::routing::get(move || {
            let counter = Arc::clone(&counter);
            async move {
                counter.fetch_add(1, Ordering::SeqCst);
                tokio::time::sleep(Duration::from_millis(50)).await;
                axum::Json(serde_json::json!({"keys": []}))
            }
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });

    let https =
        |path: &str| reqwest::Url::parse(&format!("https://appleid.example{path}")).unwrap();
    let mut config = AuthConfig::new(AuthConfigInput {
        jwt_secret: SecretString::from("x".repeat(48)),
        admin_password: SecretString::from("admin"),
        admin_session_lifetime: Duration::from_secs(60),
        access_token_lifetime: Duration::from_secs(60),
        refresh_token_lifetime: Duration::from_secs(60),
        apple_jwks_url: https("/keys"),
        apple_signin_audiences: vec!["com.example.app".to_owned()],
        apple_token_url: https("/token"),
        apple_revoke_url: https("/revoke"),
        apple_client_id: "com.example.app".to_owned(),
        apple_team_id: None,
        apple_key_id: None,
        apple_private_key: None,
    })
    .unwrap();
    config.apple_jwks_url = reqwest::Url::parse(&format!("http://{address}/keys")).unwrap();

    let lookups = (0..8).map(|index| {
        let config = config.clone();
        async move { config.apple_decoding_key(&format!("forged-{index}")).await }
    });
    for result in futures_util::future::join_all(lookups).await {
        assert!(matches!(result, Err(AppleIdentityError::UnknownKey(_))));
    }
    assert!(matches!(
        config.apple_decoding_key("forged-again").await,
        Err(AppleIdentityError::UnknownKey(_))
    ));
    assert_eq!(fetches.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn failed_apple_key_refreshes_are_also_rate_limited() {
    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::time::Duration;

    use secrecy::SecretString;

    use super::{AuthConfig, AuthConfigInput};

    let fetches = Arc::new(AtomicUsize::new(0));
    let counter = Arc::clone(&fetches);
    let app = axum::Router::new().route(
        "/keys",
        axum::routing::get(move || {
            let counter = Arc::clone(&counter);
            async move {
                counter.fetch_add(1, Ordering::SeqCst);
                tokio::time::sleep(Duration::from_millis(50)).await;
                axum::http::StatusCode::SERVICE_UNAVAILABLE
            }
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });

    let https =
        |path: &str| reqwest::Url::parse(&format!("https://appleid.example{path}")).unwrap();
    let mut config = AuthConfig::new(AuthConfigInput {
        jwt_secret: SecretString::from("x".repeat(48)),
        admin_password: SecretString::from("admin"),
        admin_session_lifetime: Duration::from_secs(60),
        access_token_lifetime: Duration::from_secs(60),
        refresh_token_lifetime: Duration::from_secs(60),
        apple_jwks_url: https("/keys"),
        apple_signin_audiences: vec!["com.example.app".to_owned()],
        apple_token_url: https("/token"),
        apple_revoke_url: https("/revoke"),
        apple_client_id: "com.example.app".to_owned(),
        apple_team_id: None,
        apple_key_id: None,
        apple_private_key: None,
    })
    .unwrap();
    config.apple_jwks_url = reqwest::Url::parse(&format!("http://{address}/keys")).unwrap();

    let lookups = (0..8).map(|index| {
        let config = config.clone();
        async move { config.apple_decoding_key(&format!("forged-{index}")).await }
    });
    for result in futures_util::future::join_all(lookups).await {
        assert!(result.is_err());
    }
    assert!(config.apple_decoding_key("forged-again").await.is_err());
    assert_eq!(fetches.load(Ordering::SeqCst), 1);
}

#[test]
fn malformed_api_key_is_unauthorized_and_database_failure_is_retryable() {
    let malformed = api_key_lookup_rejection(
        &AuthenticationRepositoryError::InvalidApiKeyFormat,
        "req-1".to_owned(),
    );
    assert_eq!(
        axum::response::IntoResponse::into_response(malformed).status(),
        StatusCode::UNAUTHORIZED
    );

    let database = api_key_lookup_rejection(
        &AuthenticationRepositoryError::Sqlx(sqlx::Error::PoolTimedOut),
        "req-2".to_owned(),
    );
    assert_eq!(
        axum::response::IntoResponse::into_response(database).status(),
        StatusCode::INTERNAL_SERVER_ERROR
    );
}
use sha2::Digest as _;

#[test]
fn bearer_parser_is_case_insensitive_and_rejects_extra_parts() {
    assert_eq!(bearer_token("Bearer token"), Some("token"));
    assert_eq!(bearer_token("bearer token"), Some("token"));
    assert_eq!(bearer_token("Basic token"), None);
    assert_eq!(bearer_token("Bearer token extra"), None);
}

#[test]
fn apple_nonce_requires_the_sha256_of_the_raw_nonce() {
    let raw_nonce = "newsly-test-nonce";
    let expected = newsly_domain::hex_encode(&sha2::Sha256::digest(raw_nonce.as_bytes()));

    assert!(verify_apple_nonce(Some(&expected), raw_nonce).is_ok());
    assert!(matches!(
        verify_apple_nonce(None, raw_nonce),
        Err(AppleIdentityError::InvalidNonce)
    ));
    assert!(matches!(
        verify_apple_nonce(Some("different"), raw_nonce),
        Err(AppleIdentityError::InvalidNonce)
    ));
    let empty_hash = newsly_domain::hex_encode(&sha2::Sha256::digest(b""));
    assert!(matches!(
        verify_apple_nonce(Some(&empty_hash), ""),
        Err(AppleIdentityError::InvalidNonce)
    ));
}
