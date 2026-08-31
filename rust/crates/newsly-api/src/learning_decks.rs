use axum::Router;
use axum::routing::{get, post};

use crate::AppState;

mod hosted;
pub(crate) mod operations;
mod presentation;
mod source;
mod support;

use hosted::{
    serve_private_asset, serve_private_deck, serve_private_source_notes, serve_shared_asset,
    serve_shared_deck, serve_shared_source_notes,
};
pub(super) use operations::{
    create_deck, create_source_notes_url, create_viewer_url, delete_deck, disable_share,
    enable_share, get_deck, list_decks, retry_deck,
};

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/api/learning/decks", get(list_decks).post(create_deck))
        .route(
            "/api/learning/decks/{deck_id}",
            get(get_deck).delete(delete_deck),
        )
        .route("/api/learning/decks/{deck_id}/retry", post(retry_deck))
        .route(
            "/api/learning/decks/{deck_id}/share",
            post(enable_share).delete(disable_share),
        )
        .route(
            "/api/learning/decks/{deck_id}/viewer-url",
            post(create_viewer_url),
        )
        .route(
            "/api/learning/decks/{deck_id}/source-notes-url",
            post(create_source_notes_url),
        )
        .route("/learning/share/{token}/", get(serve_shared_deck))
        .route(
            "/learning/share/{token}/source-notes",
            get(serve_shared_source_notes),
        )
        .route(
            "/learning/share/{token}/assets/{*asset_path}",
            get(serve_shared_asset),
        )
        .route("/learning/signed/{token}/", get(serve_private_deck))
        .route(
            "/learning/signed/{token}/source-notes",
            get(serve_private_source_notes),
        )
        .route(
            "/learning/signed/{token}/assets/{*asset_path}",
            get(serve_private_asset),
        )
}
