# client/newsly/newsly/Repositories/

Source folder: `client/newsly/newsly/Repositories`

## Purpose
Repository layer that wraps `APIClient` calls for content, read-state, and daily digest endpoints into higher-level async methods used by view models.

## Runtime behavior
- Keeps transport details out of view models by exposing feature-shaped repository methods.
- Encapsulates content feed pagination, read/unread updates, and daily digest retrieval behind stable interfaces.

## Inventory scope
- Direct file inventory for `client/newsly/newsly/Repositories`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `client/newsly/newsly/Repositories/ContentRepository.swift` | `protocol ContentRepositoryType`, `class ContentRepository`, `loadDetail`, `loadPage` | Types: `protocol ContentRepositoryType`, `class ContentRepository`. Functions: `loadDetail`, `loadPage` |
| `client/newsly/newsly/Repositories/DailyNewsDigestRepository.swift` | `protocol DailyNewsDigestRepositoryType`, `class DailyNewsDigestRepository`, `fetchVoiceSummary`, `fetchVoiceSummaryAudio`, `loadPage`, `markRead`, `markUnread`, `startDigDeeperChat` | Types: `protocol DailyNewsDigestRepositoryType`, `class DailyNewsDigestRepository`. Functions: `fetchVoiceSummary`, `fetchVoiceSummaryAudio`, `loadPage`, `markRead`, `markUnread`, `startDigDeeperChat` |
| `client/newsly/newsly/Repositories/ReadStatusRepository.swift` | `protocol ReadStatusRepositoryType`, `class ReadStatusRepository`, `struct BulkMarkReadRequest`, `enum CodingKeys`, `markRead` | Types: `protocol ReadStatusRepositoryType`, `class ReadStatusRepository`, `struct BulkMarkReadRequest`, `enum CodingKeys`. Functions: `markRead` |
