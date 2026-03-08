# News Grouped View Feature

## Overview

The news tab displays news items in groups of 5, automatically marking entire groups as read when users scroll past them. This replaces the previous infinite scroll behavior with a more digestible, batch-oriented approach.

## User Experience

### Display
- News items are shown in cards containing exactly 5 news links
- Each card shows:
  - Group header with item count
  - 5 news items with title, platform icon, and source
  - Read indicator when entire group is marked as read
  - Action buttons: Favorite and Convert

### Auto-Mark Behavior
- When user scrolls past a group (group disappears from view), all 5 items are automatically marked as read
- Groups fade out smoothly after being marked
- Unread count badge updates immediately

### Actions
- **Favorite**: Favorites the first item in the group (can be expanded to all items)
- **Convert**: Converts the news link to a full article for detailed processing

## Implementation

### Backend

**Endpoint**: `POST /api/content/{content_id}/convert-to-article`

Converts a news item to an article by:
1. Extracting article URL from news metadata
2. Checking if article already exists (de-duplication)
3. Creating new article content entry with `PENDING` status
4. Returning new article ID for navigation

**Response**:
```json
{
  "status": "success",
  "new_content_id": 123,
  "original_content_id": 456,
  "already_exists": false,
  "message": "Article created and queued for processing"
}
```

### iOS

**Models**:
- `NewsGroup`: Represents a group of 5 news items with group-level read tracking
- Extension: `Array<ContentSummary>.groupedByFive()` chunks news into groups

**ViewModels**:
- `NewsGroupViewModel`: Manages loading, pagination, and group-level operations
  - Tracks viewed groups to prevent duplicate marking
  - Handles bulk mark-as-read for groups
  - Supports favorite and convert actions

**Views**:
- `NewsGroupCard`: Displays group of 5 items with actions
- `NewsView`: Replaces infinite scroll with grouped ScrollView

**Key Behavior**:
- `.onDisappear` on groups triggers auto-mark
- Groups are removed from list after marking (smooth animation)
- Pull-to-refresh reloads groups
- Pagination loads next 25 items (creates 5 new groups)

## Testing

### Backend Tests
- `test_api_content_convert.py`: Unit tests for convert endpoint
- `test_convert_workflow.py`: Integration tests for full workflow

### iOS Testing
- Build and run in simulator
- Verify grouping displays correctly
- Test scroll-past auto-marking
- Test favorite and convert buttons
- Verify pull-to-refresh
- Check pagination loads more groups

## Configuration

No additional configuration required. Feature uses existing:
- Backend: Content API, bulk mark-as-read
- iOS: ContentService, UnreadCountService

## Future Enhancements

- Allow favoriting all items in group (not just first)
- Add group-level "convert all" option
- Support customizable group size (currently fixed at 5)
- Add swipe gestures for quick actions
- Preview article before converting
