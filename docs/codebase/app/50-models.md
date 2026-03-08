# app/models/

Source folder: `app/models`

## Purpose
Shared data model layer containing SQLAlchemy ORM tables, Pydantic request/response contracts, metadata payloads, enums, pagination types, and scraper/discovery DTOs.

## Runtime behavior
- Defines the database schema for content, tasks, chat, discovery, onboarding, favorites, read-state, and user integrations.
- Holds the typed metadata and summary contracts that workers persist into JSON columns and that presenters/routers validate on read.
- Provides queue/task enums and shared Pydantic models used across services, handlers, and API endpoints.

## Inventory scope
- Direct file inventory for `app/models`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/models/__init__.py` | n/a | Database models. |
| `app/models/content_submission.py` | `SubmitContentRequest`, `ContentSubmissionResponse` | Pydantic models for content submission workflows. |
| `app/models/contracts.py` | `ContentType`, `ContentStatus`, `ContentClassification`, `TaskType`, `TaskQueue`, `TaskStatus`, `SummaryKind`, `SummaryVersion` | Canonical domain contracts and enums shared across backend surfaces. |
| `app/models/feed_discovery.py` | `FavoriteDigest`, `DiscoveryDirection`, `DiscoveryDirectionPlan`, `DiscoveryQuery`, `DiscoveryLane`, `DiscoveryLanePlan`, `DiscoveryCandidate`, `DiscoveryCandidateBatch`, `DiscoveryRunResult` | Pydantic models for feed discovery workflow. |
| `app/models/metadata.py` | `SummaryBulletPoint`, `SummaryTextBullet`, `ContentQuote`, `InterleavedInsight`, `InterleavedSummary`, `InterleavedTopic`, `InterleavedSummaryV2`, `BulletSummaryPoint`, `BulletedSummary`, `EditorialQuote`, +15 more | Unified metadata models for content types |
| `app/models/metadata_state.py` | `normalize_metadata_shape`, `merge_runtime_metadata`, `update_processing_state` | Helpers for transitioning metadata from flat blobs to structured state |
| `app/models/pagination.py` | `PaginationCursorData`, `PaginationMetadata` | Pydantic models for pagination. |
| `app/models/schema.py` | `Content`, `ContentDiscussion`, `ProcessingTask`, `ContentReadStatus`, `ContentFavorites`, `DailyNewsDigest`, `FeedDiscoveryRun`, `FeedDiscoverySuggestion`, `OnboardingDiscoveryRun`, `OnboardingDiscoveryLane`, +11 more | Types: `Content`, `ContentDiscussion`, `ProcessingTask`, `ContentReadStatus`, `ContentFavorites`, `DailyNewsDigest`, `FeedDiscoveryRun`, `FeedDiscoverySuggestion`. +13 more |
| `app/models/scraper_runs.py` | `ScraperStats` | Types: `ScraperStats` |
| `app/models/summary_contracts.py` | `parse_summary_kind`, `parse_summary_version`, `infer_summary_kind`, `resolve_summary_kind`, `is_structured_summary_payload` | Canonical helpers for summary kind/version interpretation. |
| `app/models/user.py` | `User`, `UserBase`, `UserCreate`, `UserResponse`, `AppleSignInRequest`, `TokenResponse`, `RefreshTokenRequest`, `AccessTokenResponse`, `AdminLoginRequest`, `AdminLoginResponse`, +1 more | User models and schemas for authentication. |
