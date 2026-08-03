"""Small value objects for X API responses."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class XList:
    """Minimal X list payload used for sync."""

    id: str
    name: str


@dataclass(frozen=True)
class XUser:
    """Normalized X user profile."""

    id: str
    username: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class XTweet:
    """Normalized X tweet payload."""

    id: str
    text: str
    author_id: str | None = None
    author_username: str | None = None
    author_name: str | None = None
    created_at: str | None = None
    like_count: int | None = None
    retweet_count: int | None = None
    reply_count: int | None = None
    conversation_id: str | None = None
    in_reply_to_user_id: str | None = None
    referenced_tweet_types: list[str] = field(default_factory=list)
    article_title: str | None = None
    article_text: str | None = None
    note_tweet_text: str | None = None
    external_urls: list[str] = field(default_factory=list)
    linked_tweet_ids: list[str] = field(default_factory=list)
    has_video: bool = False
    video_duration_ms: int | None = None


@dataclass(frozen=True)
class XTokenResponse:
    """OAuth token response payload."""

    access_token: str
    refresh_token: str | None
    expires_in: int | None
    scopes: list[str]


@dataclass(frozen=True)
class XTweetFetchResult:
    """Fetch result for a tweet lookup call."""

    success: bool
    tweet: XTweet | None = None
    error: str | None = None


@dataclass(frozen=True)
class XTweetsPage:
    """Page of tweets returned from an X API collection endpoint."""

    tweets: list[XTweet]
    included_tweets: dict[str, XTweet] = field(default_factory=dict)
    next_token: str | None = None
