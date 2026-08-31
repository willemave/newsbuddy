use std::collections::{BTreeMap, BTreeSet};
use std::time::Duration;

use reqwest::{Client, Url};
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use thiserror::Error;

const X_TWEET_FIELDS: &str = "created_at,author_id,public_metrics,entities,conversation_id,in_reply_to_user_id,referenced_tweets,text,article,note_tweet,attachments";
const X_USER_FIELDS: &str = "name,username";
const X_MEDIA_FIELDS: &str = "type,duration_ms,public_metrics";
const X_TWEET_EXPANSIONS: &str =
    "author_id,referenced_tweets.id,referenced_tweets.id.author_id,attachments.media_keys";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct XTweet {
    pub id: String,
    pub text: String,
    pub author_id: Option<String>,
    pub author_username: Option<String>,
    pub author_name: Option<String>,
    pub created_at: Option<String>,
    pub like_count: Option<i64>,
    pub retweet_count: Option<i64>,
    pub reply_count: Option<i64>,
    pub conversation_id: Option<String>,
    pub in_reply_to_user_id: Option<String>,
    pub referenced_tweet_types: Vec<String>,
    pub article_title: Option<String>,
    pub article_text: Option<String>,
    pub note_tweet_text: Option<String>,
    pub external_urls: Vec<String>,
    pub linked_tweet_ids: Vec<String>,
    pub has_video: bool,
    pub video_duration_ms: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct XBookmarksPage {
    pub tweets: Vec<XTweet>,
    pub included_tweets: BTreeMap<String, XTweet>,
    pub next_token: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct XSyncToken {
    pub access_token: String,
    pub refresh_token: Option<String>,
    pub expires_in: Option<i64>,
    pub scopes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct XSyncUser {
    pub id: String,
    pub username: Option<String>,
}

#[derive(Debug, Clone)]
pub struct XSyncGateway {
    client: Client,
    client_id: String,
    client_secret: Option<SecretString>,
    token_url: Url,
    api_base_url: Url,
}

/// Read-only X post lookup adapter used by content analysis.
///
/// This is separate from [`XSyncGateway`] because app-authenticated post lookup does not require
/// OAuth client identity, token refresh, or any integration persistence.
#[derive(Debug, Clone)]
pub struct XLookupGateway {
    client: Client,
    api_base_url: Url,
}

impl XLookupGateway {
    /// Builds the bounded official-X post lookup adapter.
    ///
    /// # Errors
    ///
    /// Returns an error when the API URL is not an HTTP(S) base or the client cannot be built.
    pub fn new(api_base_url: Url) -> Result<Self, XSyncGatewayError> {
        if !matches!(api_base_url.scheme(), "http" | "https") || api_base_url.cannot_be_a_base() {
            return Err(XSyncGatewayError::InvalidConfiguration);
        }
        Ok(Self {
            client: Client::builder().timeout(Duration::from_secs(20)).build()?,
            api_base_url,
        })
    }

    /// Fetches one post with expanded author, referenced-post, and media snapshots.
    ///
    /// # Errors
    ///
    /// Returns an error for an invalid post id, provider rejection, transport failure, or an
    /// unusable provider response.
    pub async fn fetch_tweet(
        &self,
        access_token: &str,
        tweet_id: &str,
    ) -> Result<(XTweet, BTreeMap<String, XTweet>), XSyncGatewayError> {
        let tweet_id = valid_tweet_id(tweet_id)?;
        let mut url = self.api_base_url.clone();
        url.set_path(&format!(
            "{}/tweets/{tweet_id}",
            url.path().trim_end_matches('/')
        ));
        append_tweet_fields(&mut url);
        let payload = self.send_lookup(url, access_token).await?;
        map_single_tweet(&payload)
    }

    /// Fetches a bounded set of posts while preserving the provider's expanded snapshots.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid input, provider rejection, transport failure, or malformed
    /// JSON. At most 100 unique post IDs are accepted.
    pub async fn fetch_tweets(
        &self,
        access_token: &str,
        tweet_ids: &[String],
    ) -> Result<XBookmarksPage, XSyncGatewayError> {
        let mut seen = BTreeSet::new();
        let ids = tweet_ids
            .iter()
            .map(|value| valid_tweet_id(value))
            .collect::<Result<Vec<_>, _>>()?
            .into_iter()
            .filter(|value| seen.insert((*value).to_owned()))
            .collect::<Vec<_>>();
        if ids.is_empty() || ids.len() > 100 {
            return Err(XSyncGatewayError::InvalidRequest(
                "between 1 and 100 unique tweet ids are required",
            ));
        }
        let mut url = self.api_base_url.clone();
        url.set_path(&format!("{}/tweets", url.path().trim_end_matches('/')));
        append_tweet_fields(&mut url);
        url.query_pairs_mut().append_pair("ids", &ids.join(","));
        let payload = self.send_lookup(url, access_token).await?;
        Ok(map_tweets_page(&payload))
    }

    /// Searches recent posts for a same-author conversation thread.
    ///
    /// # Errors
    ///
    /// Returns an error for empty input, provider rejection, transport failure, or malformed
    /// JSON.
    pub async fn search_recent(
        &self,
        access_token: &str,
        query: &str,
        max_results: u8,
    ) -> Result<XBookmarksPage, XSyncGatewayError> {
        let query = query.trim();
        if query.is_empty() {
            return Err(XSyncGatewayError::InvalidRequest(
                "search query is required",
            ));
        }
        let mut url = self.api_base_url.clone();
        url.set_path(&format!(
            "{}/tweets/search/recent",
            url.path().trim_end_matches('/')
        ));
        append_tweet_fields(&mut url);
        url.query_pairs_mut()
            .append_pair("query", query)
            .append_pair("max_results", &max_results.clamp(10, 100).to_string());
        let payload = self.send_lookup(url, access_token).await?;
        Ok(map_tweets_page(&payload))
    }

    /// Fetches one bounded page of a user's public posts.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid input, provider rejection, transport failure, or malformed
    /// JSON.
    pub async fn fetch_user_tweets(
        &self,
        access_token: &str,
        user_id: &str,
        pagination_token: Option<&str>,
        max_results: u8,
    ) -> Result<XBookmarksPage, XSyncGatewayError> {
        let user_id = user_id.trim();
        if user_id.is_empty() || !user_id.bytes().all(|byte| byte.is_ascii_digit()) {
            return Err(XSyncGatewayError::InvalidRequest(
                "numeric X user id is required",
            ));
        }
        let mut url = self.api_base_url.clone();
        url.set_path(&format!(
            "{}/users/{user_id}/tweets",
            url.path().trim_end_matches('/')
        ));
        append_tweet_fields(&mut url);
        {
            let mut query = url.query_pairs_mut();
            query.append_pair("max_results", &max_results.clamp(5, 100).to_string());
            if let Some(token) = pagination_token
                .map(str::trim)
                .filter(|value| !value.is_empty())
            {
                query.append_pair("pagination_token", token);
            }
        }
        let payload = self.send_lookup(url, access_token).await?;
        Ok(map_tweets_page(&payload))
    }

    async fn send_lookup(
        &self,
        url: Url,
        access_token: &str,
    ) -> Result<Map<String, Value>, XSyncGatewayError> {
        let token = access_token.trim();
        let token = strip_bearer_prefix(token);
        if token.is_empty() || token.eq_ignore_ascii_case("bearer") {
            return Err(XSyncGatewayError::InvalidRequest(
                "X access token is required",
            ));
        }
        send_json(
            self.client
                .get(url)
                .header("accept", "application/json")
                .bearer_auth(token),
        )
        .await
    }
}

impl XSyncGateway {
    /// Builds the bounded official-X HTTP adapter used by background synchronization.
    ///
    /// # Errors
    ///
    /// Returns an error for missing client identity, invalid URLs, or client construction.
    pub fn new(
        client_id: &str,
        client_secret: Option<SecretString>,
        token_url: Url,
        api_base_url: Url,
    ) -> Result<Self, XSyncGatewayError> {
        if client_id.trim().is_empty()
            || [&token_url, &api_base_url]
                .iter()
                .any(|url| !matches!(url.scheme(), "http" | "https") || url.cannot_be_a_base())
        {
            return Err(XSyncGatewayError::InvalidConfiguration);
        }
        Ok(Self {
            client: Client::builder().timeout(Duration::from_secs(20)).build()?,
            client_id: client_id.trim().to_owned(),
            client_secret,
            token_url,
            api_base_url,
        })
    }

    /// Exchanges a refresh token for the current X OAuth token generation.
    ///
    /// # Errors
    ///
    /// Returns an error when the request is invalid, X rejects it, transport fails, or the
    /// response does not contain a usable access token.
    pub async fn refresh_oauth_token(
        &self,
        refresh_token: &str,
    ) -> Result<XSyncToken, XSyncGatewayError> {
        if refresh_token.trim().is_empty() {
            return Err(XSyncGatewayError::InvalidRequest(
                "refresh token is required",
            ));
        }
        let form = [
            ("grant_type", "refresh_token"),
            ("client_id", self.client_id.as_str()),
            ("refresh_token", refresh_token.trim()),
        ];
        let mut request = self
            .client
            .post(self.token_url.clone())
            .header("accept", "application/json")
            .form(&form);
        if let Some(secret) = &self.client_secret {
            request = request.basic_auth(&self.client_id, Some(secret.expose_secret()));
        }
        let payload = send_json(request).await?;
        parse_token(&payload)
    }

    /// Loads the X identity associated with an access token.
    ///
    /// # Errors
    ///
    /// Returns an error when the token is empty, X rejects the request, transport fails, or the
    /// response does not contain a provider user ID.
    pub async fn authenticated_user(
        &self,
        access_token: &str,
    ) -> Result<XSyncUser, XSyncGatewayError> {
        if access_token.trim().is_empty() {
            return Err(XSyncGatewayError::InvalidRequest(
                "access token is required",
            ));
        }
        let mut url = self.api_base_url.clone();
        url.set_path(&format!("{}/users/me", url.path().trim_end_matches('/')));
        url.query_pairs_mut()
            .append_pair("user.fields", X_USER_FIELDS);
        let payload = send_json(
            self.client
                .get(url)
                .header("accept", "application/json")
                .bearer_auth(access_token.trim()),
        )
        .await?;
        let data = payload.get("data").and_then(Value::as_object).ok_or(
            XSyncGatewayError::MalformedResponse("X /users/me response is missing data"),
        )?;
        Ok(XSyncUser {
            id: optional_string(data.get("id")).ok_or(XSyncGatewayError::MalformedResponse(
                "X /users/me response is missing user id",
            ))?,
            username: optional_string(data.get("username")),
        })
    }

    /// Fetches one page of bookmarks and the expanded tweet, author, and media snapshots.
    ///
    /// # Errors
    ///
    /// Returns an error when the request is invalid, X rejects it, transport fails, or the
    /// response is not valid JSON.
    pub async fn fetch_bookmarks(
        &self,
        access_token: &str,
        provider_user_id: &str,
        pagination_token: Option<&str>,
        max_results: u8,
    ) -> Result<XBookmarksPage, XSyncGatewayError> {
        if access_token.trim().is_empty() || provider_user_id.trim().is_empty() {
            return Err(XSyncGatewayError::InvalidRequest(
                "access token and X user id are required",
            ));
        }
        let mut url = self.api_base_url.clone();
        url.set_path(&format!(
            "{}/users/{}/bookmarks",
            url.path().trim_end_matches('/'),
            provider_user_id.trim()
        ));
        {
            let mut query = url.query_pairs_mut();
            query
                .append_pair("max_results", &max_results.clamp(5, 100).to_string())
                .append_pair("expansions", X_TWEET_EXPANSIONS)
                .append_pair("tweet.fields", X_TWEET_FIELDS)
                .append_pair("user.fields", X_USER_FIELDS)
                .append_pair("media.fields", X_MEDIA_FIELDS);
            if let Some(token) = pagination_token
                .map(str::trim)
                .filter(|value| !value.is_empty())
            {
                query.append_pair("pagination_token", token);
            }
        }
        let payload = send_json(
            self.client
                .get(url)
                .header("accept", "application/json")
                .bearer_auth(access_token.trim()),
        )
        .await?;
        Ok(map_tweets_page(&payload))
    }
}

async fn send_json(
    request: reqwest::RequestBuilder,
) -> Result<Map<String, Value>, XSyncGatewayError> {
    let response = request.send().await?;
    let status = response.status();
    let bytes = response.bytes().await?;
    if !status.is_success() {
        let detail = serde_json::from_slice::<Value>(&bytes)
            .ok()
            .and_then(|payload| payload.as_object().cloned())
            .map_or_else(
                || {
                    let text = String::from_utf8_lossy(&bytes);
                    let text = text.trim();
                    if text.is_empty() {
                        "Unknown error".to_owned()
                    } else {
                        text.chars().take(300).collect()
                    }
                },
                |object| extract_error(&object),
            );
        return Err(XSyncGatewayError::Provider {
            status: status.as_u16(),
            detail,
        });
    }
    let payload = if bytes.is_empty() {
        Value::Object(Map::new())
    } else {
        serde_json::from_slice(&bytes).map_err(|_| {
            XSyncGatewayError::MalformedResponse("X provider response is not valid JSON")
        })?
    };
    let object = payload
        .as_object()
        .cloned()
        .ok_or(XSyncGatewayError::MalformedResponse(
            "X provider response is not a JSON object",
        ))?;
    Ok(object)
}

fn parse_token(payload: &Map<String, Value>) -> Result<XSyncToken, XSyncGatewayError> {
    let access_token = optional_string(payload.get("access_token")).ok_or(
        XSyncGatewayError::MalformedResponse("X token response is missing access_token"),
    )?;
    let expires_in = payload.get("expires_in").and_then(|value| {
        value
            .as_i64()
            .or_else(|| value.as_str().and_then(|raw| raw.parse().ok()))
    });
    let scopes = match payload.get("scope") {
        Some(Value::String(raw)) => raw.split_whitespace().map(ToOwned::to_owned).collect(),
        Some(Value::Array(values)) => values
            .iter()
            .filter_map(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(ToOwned::to_owned)
            .collect(),
        _ => Vec::new(),
    };
    Ok(XSyncToken {
        access_token,
        refresh_token: optional_string(payload.get("refresh_token")),
        expires_in,
        scopes,
    })
}

fn map_tweets_page(payload: &Map<String, Value>) -> XBookmarksPage {
    let includes = payload
        .get("includes")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let users = object_lookup(includes.get("users"), "id");
    let media = object_lookup(includes.get("media"), "media_key");

    let tweets = payload
        .get("data")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_object)
        .filter_map(|tweet| map_tweet(tweet, &users, &media))
        .collect();
    let included_tweets = includes
        .get("tweets")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_object)
        .filter_map(|tweet| map_tweet(tweet, &users, &media))
        .map(|tweet| (tweet.id.clone(), tweet))
        .collect();
    let next_token = payload
        .get("meta")
        .and_then(Value::as_object)
        .and_then(|meta| optional_string(meta.get("next_token")));
    XBookmarksPage {
        tweets,
        included_tweets,
        next_token,
    }
}

fn map_single_tweet(
    payload: &Map<String, Value>,
) -> Result<(XTweet, BTreeMap<String, XTweet>), XSyncGatewayError> {
    let includes = payload
        .get("includes")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let users = object_lookup(includes.get("users"), "id");
    let media = object_lookup(includes.get("media"), "media_key");
    let tweet = payload
        .get("data")
        .and_then(Value::as_object)
        .and_then(|tweet| map_tweet(tweet, &users, &media))
        .ok_or(XSyncGatewayError::MalformedResponse(
            "X post response is missing a usable data object",
        ))?;
    let included_tweets = includes
        .get("tweets")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_object)
        .filter_map(|tweet| map_tweet(tweet, &users, &media))
        .map(|tweet| (tweet.id.clone(), tweet))
        .collect();
    Ok((tweet, included_tweets))
}

fn append_tweet_fields(url: &mut Url) {
    url.query_pairs_mut()
        .append_pair("expansions", X_TWEET_EXPANSIONS)
        .append_pair("tweet.fields", X_TWEET_FIELDS)
        .append_pair("user.fields", X_USER_FIELDS)
        .append_pair("media.fields", X_MEDIA_FIELDS);
}

fn valid_tweet_id(value: &str) -> Result<&str, XSyncGatewayError> {
    let value = value.trim();
    if value.is_empty() || !value.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(XSyncGatewayError::InvalidRequest(
            "numeric tweet id is required",
        ));
    }
    Ok(value)
}

fn map_tweet(
    tweet: &Map<String, Value>,
    users: &BTreeMap<String, Map<String, Value>>,
    media: &BTreeMap<String, Map<String, Value>>,
) -> Option<XTweet> {
    let id = optional_string(tweet.get("id"))?;
    let (article_title, article_text) = article_parts(tweet.get("article"));
    let note_tweet_text = note_tweet_text(tweet.get("note_tweet"));
    let text = optional_string(tweet.get("text"))
        .or_else(|| note_tweet_text.clone())
        .or_else(|| article_title.clone())
        .or_else(|| article_text.clone())?;
    let author_id = optional_string(tweet.get("author_id"));
    let author = author_id
        .as_ref()
        .and_then(|author_id| users.get(author_id));
    let author_username = author.and_then(|value| optional_string(value.get("username")));
    let author_name = author
        .and_then(|value| optional_string(value.get("name")))
        .or_else(|| author_username.clone());
    let metrics = tweet.get("public_metrics").and_then(Value::as_object);
    let entities = tweet.get("entities").and_then(Value::as_object);
    let referenced_tweet_types = tweet
        .get("referenced_tweets")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_object)
        .filter_map(|reference| optional_string(reference.get("type")))
        .collect();
    let external_urls = external_urls(entities);
    let linked_tweet_ids = linked_tweet_ids(tweet, entities);
    let (has_video, video_duration_ms) = video_metadata(tweet, media);
    Some(XTweet {
        id,
        text,
        author_id,
        author_username,
        author_name,
        created_at: optional_string(tweet.get("created_at")),
        like_count: metric(metrics, "like_count"),
        retweet_count: metric(metrics, "retweet_count"),
        reply_count: metric(metrics, "reply_count"),
        conversation_id: optional_string(tweet.get("conversation_id")),
        in_reply_to_user_id: optional_string(tweet.get("in_reply_to_user_id")),
        referenced_tweet_types,
        article_title,
        article_text,
        note_tweet_text,
        external_urls,
        linked_tweet_ids,
        has_video,
        video_duration_ms,
    })
}

fn object_lookup(value: Option<&Value>, key: &str) -> BTreeMap<String, Map<String, Value>> {
    value
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_object)
        .filter_map(|object| optional_string(object.get(key)).map(|id| (id, object.clone())))
        .collect()
}

fn article_parts(value: Option<&Value>) -> (Option<String>, Option<String>) {
    let Some(article_data) = value.and_then(Value::as_object) else {
        return (None, None);
    };
    let article_result = article_data
        .get("article_results")
        .and_then(Value::as_object)
        .and_then(|results| results.get("result"))
        .and_then(Value::as_object)
        .or_else(|| article_data.get("result").and_then(Value::as_object))
        .unwrap_or(article_data);
    let title = first_text([
        article_result.get("title"),
        article_result.get("headline"),
        article_data.get("title"),
        article_data.get("headline"),
    ]);
    let mut body = first_text([
        article_result.get("plain_text"),
        article_data.get("plain_text"),
        nested_value(article_result, &["body", "text"]),
        nested_value(article_result, &["body", "richtext", "text"]),
        nested_value(article_result, &["body", "rich_text", "text"]),
        nested_value(article_result, &["content", "text"]),
        nested_value(article_result, &["content", "richtext", "text"]),
        nested_value(article_result, &["content", "rich_text", "text"]),
        article_result.get("text"),
        nested_value(article_result, &["richtext", "text"]),
        nested_value(article_result, &["rich_text", "text"]),
        nested_value(article_data, &["body", "text"]),
        nested_value(article_data, &["body", "richtext", "text"]),
        nested_value(article_data, &["body", "rich_text", "text"]),
        nested_value(article_data, &["content", "text"]),
        nested_value(article_data, &["content", "richtext", "text"]),
        nested_value(article_data, &["content", "rich_text", "text"]),
        article_data.get("text"),
        nested_value(article_data, &["richtext", "text"]),
        nested_value(article_data, &["rich_text", "text"]),
    ]);
    if body == title {
        body = None;
    }
    if body.is_none() {
        let mut collected = Vec::new();
        collect_text_fields(&Value::Object(article_result.clone()), &mut collected);
        collect_text_fields(&Value::Object(article_data.clone()), &mut collected);
        let mut seen = BTreeSet::new();
        let unique = collected
            .into_iter()
            .filter(|item| title.as_deref() != Some(item.as_str()))
            .filter(|item| seen.insert(item.clone()))
            .collect::<Vec<_>>();
        if !unique.is_empty() {
            body = Some(unique.join("\n\n"));
        }
    }
    (title, body)
}

fn note_tweet_text(value: Option<&Value>) -> Option<String> {
    let note_data = value.and_then(Value::as_object)?;
    let note_result = note_data
        .get("note_tweet_results")
        .and_then(Value::as_object)
        .and_then(|results| results.get("result"))
        .and_then(Value::as_object)
        .or_else(|| note_data.get("result").and_then(Value::as_object))
        .unwrap_or(note_data);
    first_text([
        note_result.get("text"),
        nested_value(note_result, &["richtext", "text"]),
        nested_value(note_result, &["rich_text", "text"]),
        nested_value(note_result, &["content", "text"]),
        nested_value(note_result, &["content", "richtext", "text"]),
        nested_value(note_result, &["content", "rich_text", "text"]),
        note_data.get("text"),
        nested_value(note_data, &["richtext", "text"]),
        nested_value(note_data, &["rich_text", "text"]),
        nested_value(note_data, &["content", "text"]),
        nested_value(note_data, &["content", "richtext", "text"]),
        nested_value(note_data, &["content", "rich_text", "text"]),
    ])
}

fn metric(metrics: Option<&Map<String, Value>>, key: &str) -> Option<i64> {
    metrics.and_then(|metrics| {
        metrics.get(key).and_then(|value| {
            value.as_i64().or_else(|| {
                let raw = value.as_str()?;
                raw.bytes()
                    .all(|byte| byte.is_ascii_digit())
                    .then(|| raw.parse().ok())
                    .flatten()
            })
        })
    })
}

fn external_urls(entities: Option<&Map<String, Value>>) -> Vec<String> {
    let mut seen = BTreeSet::new();
    entities
        .and_then(|entities| entities.get("urls"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_object)
        .filter_map(|url| {
            ["expanded_url", "unwound_url", "url"]
                .into_iter()
                .find_map(|key| optional_string(url.get(key)))
        })
        .filter_map(|url| normalize_external_url(&url))
        .filter(|url| seen.insert(url.clone()))
        .collect()
}

fn normalize_external_url(value: &str) -> Option<String> {
    let mut url = Url::parse(value.trim()).ok()?;
    if !matches!(url.scheme(), "http" | "https") {
        return None;
    }
    let host = url
        .host_str()?
        .trim_end_matches('.')
        .trim_start_matches("www.")
        .to_ascii_lowercase();
    if ["x.com", "twitter.com", "t.co"]
        .iter()
        .any(|domain| host == *domain || host.ends_with(&format!(".{domain}")))
    {
        return None;
    }
    if url.scheme() != "https" && url.set_scheme("https").is_err() {
        return None;
    }
    Some(url.to_string())
}

fn strip_bearer_prefix(value: &str) -> &str {
    value
        .get(..7)
        .filter(|prefix| prefix.eq_ignore_ascii_case("bearer "))
        .and_then(|_| value.get(7..))
        .unwrap_or(value)
        .trim()
}

fn nested_value<'a>(object: &'a Map<String, Value>, path: &[&str]) -> Option<&'a Value> {
    let mut current = path.first().and_then(|key| object.get(*key))?;
    for key in path.iter().skip(1) {
        current = current.as_object()?.get(*key)?;
    }
    Some(current)
}

fn first_text<'a>(values: impl IntoIterator<Item = Option<&'a Value>>) -> Option<String> {
    values.into_iter().find_map(optional_string)
}

fn collect_text_fields(value: &Value, output: &mut Vec<String>) {
    match value {
        Value::Object(object) => {
            for (key, value) in object {
                if matches!(key.as_str(), "plain_text" | "text")
                    && let Some(text) = optional_string(Some(value))
                {
                    output.push(text);
                    continue;
                }
                collect_text_fields(value, output);
            }
        }
        Value::Array(values) => {
            for value in values {
                collect_text_fields(value, output);
            }
        }
        _ => {}
    }
}

fn linked_tweet_ids(
    tweet: &Map<String, Value>,
    entities: Option<&Map<String, Value>>,
) -> Vec<String> {
    let mut ids = Vec::new();
    let mut seen = BTreeSet::new();
    for id in tweet
        .get("referenced_tweets")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_object)
        .filter_map(|reference| optional_string(reference.get("id")))
        .filter(|id| id.bytes().all(|byte| byte.is_ascii_digit()))
    {
        if seen.insert(id.clone()) {
            ids.push(id);
        }
    }
    for url in entities
        .and_then(|entities| entities.get("urls"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_object)
        .filter_map(|url| {
            ["expanded_url", "unwound_url", "url"]
                .into_iter()
                .find_map(|key| optional_string(url.get(key)))
        })
    {
        if let Some(id) = tweet_id_from_url(&url)
            && seen.insert(id.clone())
        {
            ids.push(id);
        }
    }
    ids
}

fn tweet_id_from_url(value: &str) -> Option<String> {
    let url = Url::parse(value).ok()?;
    let host = url.host_str()?.to_ascii_lowercase();
    if !matches!(
        host.as_str(),
        "x.com" | "www.x.com" | "twitter.com" | "www.twitter.com"
    ) {
        return None;
    }
    let segments = url.path_segments()?.collect::<Vec<_>>();
    let status_index = segments.iter().position(|segment| *segment == "status")?;
    let id = segments.get(status_index + 1)?.trim();
    (!id.is_empty() && id.bytes().all(|byte| byte.is_ascii_digit())).then(|| id.to_owned())
}

fn video_metadata(
    tweet: &Map<String, Value>,
    media: &BTreeMap<String, Map<String, Value>>,
) -> (bool, Option<i64>) {
    let mut has_video = false;
    let mut max_duration = None;
    for key in tweet
        .get("attachments")
        .and_then(Value::as_object)
        .and_then(|attachments| attachments.get("media_keys"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|value| optional_string(Some(value)))
    {
        let Some(item) = media.get(&key) else {
            continue;
        };
        if optional_string(item.get("type")).as_deref() != Some("video") {
            continue;
        }
        has_video = true;
        if let Some(duration) = item.get("duration_ms").and_then(Value::as_i64)
            && duration >= 0
        {
            max_duration =
                Some(max_duration.map_or(duration, |current: i64| current.max(duration)));
        }
    }
    (has_video, max_duration)
}

fn optional_string(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
}

fn extract_error(payload: &Map<String, Value>) -> String {
    if let (Some(title), Some(detail)) = (
        payload.get("title").and_then(Value::as_str),
        payload.get("detail").and_then(Value::as_str),
    ) {
        return format!("{title}: {detail}");
    }
    for key in ["detail", "error"] {
        if let Some(message) = payload.get(key).and_then(Value::as_str) {
            return message.chars().take(300).collect();
        }
    }
    if let Some(message) = payload
        .get("errors")
        .and_then(Value::as_array)
        .and_then(|errors| errors.first())
        .and_then(Value::as_object)
        .and_then(|error| {
            ["message", "detail", "title"]
                .into_iter()
                .find_map(|key| error.get(key).and_then(Value::as_str))
        })
    {
        return message.chars().take(300).collect();
    }
    "Unknown error".to_owned()
}

#[derive(Debug, Error)]
pub enum XSyncGatewayError {
    #[error("X sync configuration is invalid")]
    InvalidConfiguration,
    #[error("X sync request is invalid: {0}")]
    InvalidRequest(&'static str),
    #[error("X API {status}: {detail}")]
    Provider { status: u16, detail: String },
    #[error("{0}")]
    MalformedResponse(&'static str),
    #[error("X provider transport failed")]
    Transport(#[from] reqwest::Error),
}

#[cfg(test)]
mod tests {
    use super::{normalize_external_url, strip_bearer_prefix, tweet_id_from_url};

    #[test]
    fn accepts_case_insensitive_bearer_prefix_without_forwarding_it_twice() {
        assert_eq!(strip_bearer_prefix("Bearer token"), "token");
        assert_eq!(strip_bearer_prefix("bearer token"), "token");
        assert_eq!(strip_bearer_prefix("Bearer "), "");
        assert_eq!(strip_bearer_prefix("token"), "token");
    }

    #[test]
    fn tweet_id_parser_accepts_only_x_status_urls() {
        assert_eq!(
            tweet_id_from_url("https://x.com/newsly/status/1234567890?ref=share").as_deref(),
            Some("1234567890")
        );
        assert_eq!(
            tweet_id_from_url("https://example.com/newsly/status/1234567890"),
            None
        );
        assert_eq!(
            tweet_id_from_url("https://x.com/newsly/status/not-a-number"),
            None
        );
    }

    #[test]
    fn external_url_normalization_rejects_non_http_and_x_hosts() {
        assert_eq!(normalize_external_url("ftp://example.com/file"), None);
        assert_eq!(normalize_external_url("https://x.com/i/status/123"), None);
        assert_eq!(
            normalize_external_url("http://example.com/story#discussion").as_deref(),
            Some("https://example.com/story#discussion")
        );
    }
}
