# News Grouped View and Convert Implementation Plan

> **For Claude:** Use `${SUPERPOWERS_SKILLS_ROOT}/skills/collaboration/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** Transform the news tab from infinite scroll to grouped sets of 5 news links, auto-marking all 5 as read when scrolling past, and replacing "mark as read"/"unlike" buttons with "favorite" and "convert to article" actions.

**Architecture:** Create new iOS views and models specifically for the news grouped display pattern. Add backend endpoint to convert news links into full articles by creating new Content entries. Update the news tab to use a paginated, group-based approach with scroll detection for auto-marking sets as read.

**Tech Stack:**
- iOS: SwiftUI, Combine
- Backend: FastAPI, SQLAlchemy, Pydantic v2
- API: RESTful JSON endpoints

---

## Task 1: Backend - Add Convert News Link Endpoint

**Files:**
- Modify: `app/routers/api_content.py` (add endpoint around line 1545)
- Modify: `app/services/ContentService.swift:161-174` (add client method)
- Modify: `app/services/APIEndpoints.swift:39-41` (add endpoint definition)

**Step 1: Write failing test for convert endpoint**

Create: `app/tests/routers/test_api_content_convert.py`

```python
"""Tests for news link to article conversion endpoint."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content


def test_convert_news_link_to_article(client: TestClient, db: Session) -> None:
    """Test converting a news link to a full article."""
    # Create a news item with article URL
    news = Content(
        url="https://news.ycombinator.com/item?id=12345",
        content_type=ContentType.NEWS.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "article": {
                "url": "https://example.com/article",
                "title": "Test Article",
                "source_domain": "example.com"
            },
            "summary": {
                "title": "News Summary",
                "overview": "This is a news summary"
            }
        },
    )
    db.add(news)
    db.commit()
    db.refresh(news)

    # Convert to article
    response = client.post(f"/api/content/{news.id}/convert-to-article")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "success"
    assert "new_content_id" in data
    assert data["original_content_id"] == news.id

    # Verify new article was created
    new_article = db.query(Content).filter(Content.id == data["new_content_id"]).first()
    assert new_article is not None
    assert new_article.content_type == ContentType.ARTICLE.value
    assert new_article.url == "https://example.com/article"
    assert new_article.status == ContentStatus.PENDING.value


def test_convert_news_link_no_article_url(client: TestClient, db: Session) -> None:
    """Test converting news link without article URL fails gracefully."""
    news = Content(
        url="https://news.ycombinator.com/item?id=12345",
        content_type=ContentType.NEWS.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "summary": {
                "title": "News Summary"
            }
        },
    )
    db.add(news)
    db.commit()
    db.refresh(news)

    response = client.post(f"/api/content/{news.id}/convert-to-article")
    assert response.status_code == 400
    assert "no article URL" in response.json()["detail"].lower()


def test_convert_non_news_content(client: TestClient, db: Session) -> None:
    """Test that converting non-news content fails."""
    article = Content(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
    )
    db.add(article)
    db.commit()
    db.refresh(article)

    response = client.post(f"/api/content/{article.id}/convert-to-article")
    assert response.status_code == 400
    assert "only news" in response.json()["detail"].lower()


def test_convert_already_exists(client: TestClient, db: Session) -> None:
    """Test converting when article already exists returns existing ID."""
    article_url = "https://example.com/article"

    # Create existing article
    existing = Content(
        url=article_url,
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.COMPLETED.value,
    )
    db.add(existing)
    db.commit()
    db.refresh(existing)

    # Create news item pointing to same URL
    news = Content(
        url="https://news.ycombinator.com/item?id=12345",
        content_type=ContentType.NEWS.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "article": {"url": article_url}
        },
    )
    db.add(news)
    db.commit()
    db.refresh(news)

    response = client.post(f"/api/content/{news.id}/convert-to-article")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "success"
    assert data["new_content_id"] == existing.id
    assert data["already_exists"] is True
```

**Step 2: Run test to verify it fails**

Run: `. .venv/bin/activate && pytest app/tests/routers/test_api_content_convert.py -v`
Expected: FAIL with endpoint not found or function not defined

**Step 3: Implement backend convert endpoint**

Add to `app/routers/api_content.py` after line 1544:

```python
@router.post(
    "/{content_id}/convert-to-article",
    summary="Convert news link to article",
    description=(
        "Convert a news content item to a full article by extracting the article URL "
        "from the news metadata and creating a new article content entry. "
        "If the article already exists, returns the existing article ID."
    ),
    responses={
        200: {"description": "News link converted successfully"},
        400: {"description": "Content cannot be converted (not news or no article URL)"},
        404: {"description": "Content not found"},
    },
)
async def convert_news_to_article(
    content_id: Annotated[int, Path(..., description="News content ID", gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
) -> dict:
    """Convert a news link to a full article content entry.

    Extracts the article URL from the news metadata and creates a new
    article content entry for processing. If an article with that URL
    already exists, returns the existing article ID instead of creating
    a duplicate.
    """
    # Check if content exists
    content = db.query(Content).filter(Content.id == content_id).first()
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    # Verify content is news type
    if content.content_type != ContentType.NEWS.value:
        raise HTTPException(
            status_code=400,
            detail="Only news content can be converted to articles"
        )

    # Extract article URL from metadata
    metadata = content.content_metadata or {}
    article_meta = metadata.get("article", {})
    article_url = article_meta.get("url")

    if not article_url:
        raise HTTPException(
            status_code=400,
            detail="No article URL found in news metadata"
        )

    # Check if article already exists
    existing_article = (
        db.query(Content)
        .filter(Content.url == article_url)
        .filter(Content.content_type == ContentType.ARTICLE.value)
        .first()
    )

    if existing_article:
        return {
            "status": "success",
            "new_content_id": existing_article.id,
            "original_content_id": content_id,
            "already_exists": True,
            "message": "Article already exists in system"
        }

    # Create new article content entry
    article_title = article_meta.get("title")
    source_domain = article_meta.get("source_domain")

    new_article = Content(
        url=article_url,
        content_type=ContentType.ARTICLE.value,
        status=ContentStatus.PENDING.value,
        title=article_title,
        source=source_domain,
        platform=None,  # Will be determined during processing
        content_metadata={},
        classification=None,
    )

    db.add(new_article)
    db.commit()
    db.refresh(new_article)

    return {
        "status": "success",
        "new_content_id": new_article.id,
        "original_content_id": content_id,
        "already_exists": False,
        "message": "Article created and queued for processing"
    }
```

**Step 4: Run test to verify it passes**

Run: `. .venv/bin/activate && pytest app/tests/routers/test_api_content_convert.py -v`
Expected: PASS for all test cases

**Step 5: Commit backend convert endpoint**

```bash
cd /Users/willem/Development/news_app
git add app/routers/api_content.py app/tests/routers/test_api_content_convert.py
git commit -m "feat: add convert news link to article endpoint

- Add POST /api/content/{id}/convert-to-article endpoint
- Extract article URL from news metadata
- Create new article content entry for processing
- Handle duplicate articles gracefully
- Add comprehensive test coverage

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: iOS - Add Convert API Method

**Files:**
- Modify: `client/newsly/newsly/Services/APIEndpoints.swift:39-41`
- Modify: `client/newsly/newsly/Services/ContentService.swift:161-174`

**Step 1: Add API endpoint constant**

Modify `client/newsly/newsly/Services/APIEndpoints.swift`, add after line 39:

```swift
static func convertNewsToArticle(id: Int) -> String {
    return "/api/content/\(id)/convert-to-article"
}
```

**Step 2: Add ContentService method**

Modify `client/newsly/newsly/Services/ContentService.swift`, add after line 174:

```swift
func convertNewsToArticle(id: Int) async throws -> ConvertNewsResponse {
    return try await client.request(
        APIEndpoints.convertNewsToArticle(id: id),
        method: "POST"
    )
}
```

**Step 3: Add response model**

Add to `client/newsly/newsly/Services/ContentService.swift` after line 22:

```swift
struct ConvertNewsResponse: Codable {
    let status: String
    let newContentId: Int
    let originalContentId: Int
    let alreadyExists: Bool
    let message: String

    enum CodingKeys: String, CodingKey {
        case status
        case newContentId = "new_content_id"
        case originalContentId = "original_content_id"
        case alreadyExists = "already_exists"
        case message
    }
}
```

**Step 4: Build iOS app to verify no compilation errors**

Run: `cd /Users/willem/Development/news_app/client/newsly && xcodebuild -project newsly.xcodeproj -scheme newsly -destination 'platform=iOS Simulator,name=iPhone 15' build`
Expected: BUILD SUCCEEDED

**Step 5: Commit iOS API changes**

```bash
cd /Users/willem/Development/news_app
git add client/newsly/newsly/Services/APIEndpoints.swift client/newsly/newsly/Services/ContentService.swift
git commit -m "feat(ios): add convert news to article API method

- Add convertNewsToArticle endpoint definition
- Add ConvertNewsResponse model
- Add ContentService.convertNewsToArticle method

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: iOS - Create NewsGroup Model

**Files:**
- Create: `client/newsly/newsly/Models/NewsGroup.swift`

**Step 1: Create NewsGroup model file**

Create `client/newsly/newsly/Models/NewsGroup.swift`:

```swift
//
//  NewsGroup.swift
//  newsly
//
//  Created by Assistant on 10/12/25.
//

import Foundation

/// Represents a group of 5 news items displayed together
struct NewsGroup: Identifiable {
    let id: String
    let items: [ContentSummary]
    var isRead: Bool

    init(items: [ContentSummary]) {
        // Use the first item's ID as the group ID
        self.id = items.first.map { "\($0.id)" } ?? UUID().uuidString
        self.items = items
        // Group is read if ALL items are read
        self.isRead = items.allSatisfy { $0.isRead }
    }

    /// Update read status for all items in group
    func updatingAllAsRead(_ read: Bool) -> NewsGroup {
        let updatedItems = items.map { $0.updating(isRead: read) }
        return NewsGroup(items: updatedItems)
    }

    /// Update a single item in the group
    func updatingItem(id: Int, with updater: (ContentSummary) -> ContentSummary) -> NewsGroup {
        let updatedItems = items.map { item in
            item.id == id ? updater(item) : item
        }
        return NewsGroup(items: updatedItems)
    }
}

extension Array where Element == ContentSummary {
    /// Group news items into groups of 5
    func groupedByFive() -> [NewsGroup] {
        var groups: [NewsGroup] = []
        for index in stride(from: 0, to: count, by: 5) {
            let endIndex = min(index + 5, count)
            let groupItems = Array(self[index..<endIndex])
            groups.append(NewsGroup(items: groupItems))
        }
        return groups
    }
}
```

**Step 2: Add NewsGroup to Xcode project**

Run: `cd /Users/willem/Development/news_app/client/newsly && open newsly.xcodeproj`
Then manually add the file to the Models group in Xcode, or run:

```bash
# Note: This is a manual step - Xcode project files are complex
# The file needs to be added to the project via Xcode IDE
echo "⚠️  MANUAL STEP: Add NewsGroup.swift to Xcode project in Models group"
```

**Step 3: Build to verify compilation**

Run: `cd /Users/willem/Development/news_app/client/newsly && xcodebuild -project newsly.xcodeproj -scheme newsly -destination 'platform=iOS Simulator,name=iPhone 15' build`
Expected: BUILD SUCCEEDED

**Step 4: Commit NewsGroup model**

```bash
cd /Users/willem/Development/news_app
git add client/newsly/newsly/Models/NewsGroup.swift
git commit -m "feat(ios): add NewsGroup model for grouped news display

- Group news items in sets of 5
- Track read status at group level
- Support item updates within groups
- Add groupedByFive() extension method

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: iOS - Create NewsGroupCard Component

**Files:**
- Create: `client/newsly/newsly/Views/Components/NewsGroupCard.swift`

**Step 1: Create NewsGroupCard view file**

Create `client/newsly/newsly/Views/Components/NewsGroupCard.swift`:

```swift
//
//  NewsGroupCard.swift
//  newsly
//
//  Created by Assistant on 10/12/25.
//

import SwiftUI

struct NewsGroupCard: View {
    let group: NewsGroup
    let onMarkAllAsRead: () async -> Void
    let onToggleFavorite: (Int) async -> Void
    let onConvert: (Int) async -> Void

    @State private var isMarkingAll = false
    @State private var favoriteStates: [Int: Bool] = [:]
    @State private var convertingStates: [Int: Bool] = [:]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Group header with count
            HStack {
                Text("News Digest")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(.secondary)

                Spacer()

                Text("\(group.items.count) items")
                    .font(.caption2)
                    .foregroundColor(.secondary)

                if group.isRead {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.caption)
                        .foregroundColor(.green)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)

            // News items
            ForEach(group.items) { item in
                VStack(alignment: .leading, spacing: 4) {
                    Text(item.displayTitle)
                        .font(.subheadline)
                        .foregroundColor(item.isRead ? .secondary : .primary)
                        .lineLimit(2)

                    HStack(spacing: 6) {
                        PlatformIcon(platform: item.platform)
                            .opacity(item.platform == nil ? 0 : 1)
                        if let source = item.source {
                            Text(source)
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                        }
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 4)

                if item.id != group.items.last?.id {
                    Divider()
                        .padding(.horizontal, 16)
                }
            }

            // Action buttons
            HStack(spacing: 16) {
                // Favorite button
                Button(action: {
                    Task {
                        if let firstItemId = group.items.first?.id {
                            await onToggleFavorite(firstItemId)
                        }
                    }
                }) {
                    HStack {
                        Image(systemName: group.items.first?.isFavorited == true ? "star.fill" : "star")
                        Text("Favorite")
                    }
                    .font(.subheadline)
                    .foregroundColor(.blue)
                }
                .buttonStyle(.borderless)
                .frame(maxWidth: .infinity)

                Divider()
                    .frame(height: 20)

                // Convert button
                Button(action: {
                    Task {
                        if let firstItemId = group.items.first?.id {
                            convertingStates[firstItemId] = true
                            await onConvert(firstItemId)
                            convertingStates[firstItemId] = false
                        }
                    }
                }) {
                    HStack {
                        if convertingStates[group.items.first?.id ?? 0] == true {
                            ProgressView()
                                .scaleEffect(0.8)
                        } else {
                            Image(systemName: "arrow.right.circle")
                        }
                        Text("Convert")
                    }
                    .font(.subheadline)
                    .foregroundColor(.blue)
                }
                .buttonStyle(.borderless)
                .frame(maxWidth: .infinity)
                .disabled(convertingStates[group.items.first?.id ?? 0] == true)
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 12)
            .padding(.top, 8)
        }
        .background(Color(.systemBackground))
        .cornerRadius(12)
        .shadow(color: Color.black.opacity(0.1), radius: 4, x: 0, y: 2)
        .opacity(group.isRead ? 0.7 : 1.0)
    }
}
```

**Step 2: Add to Xcode project**

Run: `echo "⚠️  MANUAL STEP: Add NewsGroupCard.swift to Xcode project in Views/Components group"`

**Step 3: Build to verify compilation**

Run: `cd /Users/willem/Development/news_app/client/newsly && xcodebuild -project newsly.xcodeproj -scheme newsly -destination 'platform=iOS Simulator,name=iPhone 15' build`
Expected: BUILD SUCCEEDED

**Step 4: Commit NewsGroupCard component**

```bash
cd /Users/willem/Development/news_app
git add client/newsly/newsly/Views/Components/NewsGroupCard.swift
git commit -m "feat(ios): add NewsGroupCard component for grouped display

- Display 5 news items in grouped card format
- Show group header with item count and read indicator
- Add favorite and convert action buttons
- Apply visual dimming to read groups
- Use consistent styling with shadow and corner radius

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: iOS - Create NewsGroupViewModel

**Files:**
- Create: `client/newsly/newsly/ViewModels/NewsGroupViewModel.swift`

**Step 1: Create NewsGroupViewModel file**

Create `client/newsly/newsly/ViewModels/NewsGroupViewModel.swift`:

```swift
//
//  NewsGroupViewModel.swift
//  newsly
//
//  Created by Assistant on 10/12/25.
//

import Foundation
import SwiftUI

@MainActor
class NewsGroupViewModel: ObservableObject {
    @Published var newsGroups: [NewsGroup] = []
    @Published var isLoading = false
    @Published var isLoadingMore = false
    @Published var errorMessage: String?

    // Pagination state
    @Published var nextCursor: String?
    @Published var hasMore: Bool = false

    private let contentService = ContentService.shared
    private let unreadCountService = UnreadCountService.shared

    // Track which groups have been scrolled past
    private var viewedGroupIds = Set<String>()

    func loadNewsGroups() async {
        isLoading = true
        errorMessage = nil
        nextCursor = nil
        hasMore = false
        viewedGroupIds.removeAll()

        do {
            // Load news content (limit 25 to get 5 groups)
            let response = try await contentService.fetchContentList(
                contentType: "news",
                date: nil,
                readFilter: "unread",
                cursor: nil,
                limit: 25
            )

            // Group items by 5
            newsGroups = response.contents.groupedByFive()
            nextCursor = response.nextCursor
            hasMore = response.hasMore
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    func loadMoreGroups() async {
        guard !isLoadingMore, !isLoading, hasMore, let cursor = nextCursor else {
            return
        }

        isLoadingMore = true

        do {
            let response = try await contentService.fetchContentList(
                contentType: "news",
                date: nil,
                readFilter: "unread",
                cursor: cursor,
                limit: 25
            )

            // Append new groups
            let newGroups = response.contents.groupedByFive()
            newsGroups.append(contentsOf: newGroups)
            nextCursor = response.nextCursor
            hasMore = response.hasMore
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoadingMore = false
    }

    func markGroupAsRead(_ groupId: String) async {
        guard let groupIndex = newsGroups.firstIndex(where: { $0.id == groupId }) else {
            return
        }

        let group = newsGroups[groupIndex]
        let itemIds = group.items.map { $0.id }

        do {
            _ = try await contentService.bulkMarkAsRead(contentIds: itemIds)

            // Update local state
            newsGroups[groupIndex] = group.updatingAllAsRead(true)

            // Update unread counts
            unreadCountService.decrementNewsCount(by: itemIds.count)

            // Remove from list after short delay (smooth UX)
            try? await Task.sleep(nanoseconds: 500_000_000) // 0.5 seconds
            withAnimation(.easeOut(duration: 0.3)) {
                newsGroups.remove(at: groupIndex)
            }
        } catch {
            errorMessage = "Failed to mark group as read: \(error.localizedDescription)"
        }
    }

    func onGroupScrolledPast(_ groupId: String) async {
        // Only mark once per group
        guard !viewedGroupIds.contains(groupId) else {
            return
        }

        viewedGroupIds.insert(groupId)
        await markGroupAsRead(groupId)
    }

    func toggleFavorite(_ contentId: Int) async {
        // Find group and item
        guard let groupIndex = newsGroups.firstIndex(where: { $0.items.contains(where: { $0.id == contentId }) }) else {
            return
        }

        let group = newsGroups[groupIndex]
        guard let item = group.items.first(where: { $0.id == contentId }) else {
            return
        }

        // Optimistically update
        newsGroups[groupIndex] = group.updatingItem(id: contentId) { item in
            item.updating(isFavorited: !item.isFavorited)
        }

        do {
            let response = try await contentService.toggleFavorite(id: contentId)

            // Update with server response
            if let isFavorited = response["is_favorited"] as? Bool {
                newsGroups[groupIndex] = group.updatingItem(id: contentId) { item in
                    item.updating(isFavorited: isFavorited)
                }
            }
        } catch {
            // Revert on error
            newsGroups[groupIndex] = group.updatingItem(id: contentId) { item in
                item.updating(isFavorited: !item.isFavorited)
            }
            errorMessage = "Failed to toggle favorite"
        }
    }

    func convertToArticle(_ contentId: Int) async {
        do {
            let response = try await contentService.convertNewsToArticle(id: contentId)

            // Show success message or navigate to new article
            // For now, just log success
            print("Converted to article: \(response.newContentId), already exists: \(response.alreadyExists)")

            // Optionally: Navigate to the article detail view
            // or show a toast notification
        } catch {
            errorMessage = "Failed to convert: \(error.localizedDescription)"
        }
    }

    func refresh() async {
        nextCursor = nil
        hasMore = false
        await loadNewsGroups()
    }
}
```

**Step 2: Add to Xcode project**

Run: `echo "⚠️  MANUAL STEP: Add NewsGroupViewModel.swift to Xcode project in ViewModels group"`

**Step 3: Build to verify compilation**

Run: `cd /Users/willem/Development/news_app/client/newsly && xcodebuild -project newsly.xcodeproj -scheme newsly -destination 'platform=iOS Simulator,name=iPhone 15' build`
Expected: BUILD SUCCEEDED

**Step 4: Commit NewsGroupViewModel**

```bash
cd /Users/willem/Development/news_app
git add client/newsly/newsly/ViewModels/NewsGroupViewModel.swift
git commit -m "feat(ios): add NewsGroupViewModel for grouped news logic

- Load news in groups of 5
- Auto-mark groups as read when scrolled past
- Support pagination for loading more groups
- Handle favorite and convert actions
- Track viewed groups to prevent duplicate marking
- Update unread counts on mark as read

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: iOS - Update NewsView to Use Grouped Display

**Files:**
- Modify: `client/newsly/newsly/Views/NewsView.swift:1-137`

**Step 1: Replace NewsView implementation**

Modify `client/newsly/newsly/Views/NewsView.swift`, replace entire file:

```swift
//
//  NewsView.swift
//  newsly
//
//  Created by Assistant on 9/20/25.
//  Updated by Assistant on 10/12/25 for grouped display
//

import SwiftUI

struct NewsView: View {
    @StateObject private var viewModel = NewsGroupViewModel()

    var body: some View {
        NavigationStack {
            ZStack {
                VStack(spacing: 0) {
                    if viewModel.isLoading && viewModel.newsGroups.isEmpty {
                        LoadingView()
                    } else if let error = viewModel.errorMessage, viewModel.newsGroups.isEmpty {
                        ErrorView(message: error) {
                            Task { await viewModel.loadNewsGroups() }
                        }
                    } else if viewModel.newsGroups.isEmpty {
                        VStack(spacing: 16) {
                            Spacer()
                            Image(systemName: "newspaper")
                                .font(.largeTitle)
                                .foregroundColor(.secondary)
                            Text("No news items found.")
                                .foregroundColor(.secondary)
                            Spacer()
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else {
                        ScrollView {
                            LazyVStack(spacing: 16) {
                                ForEach(viewModel.newsGroups) { group in
                                    NewsGroupCard(
                                        group: group,
                                        onMarkAllAsRead: {
                                            await viewModel.markGroupAsRead(group.id)
                                        },
                                        onToggleFavorite: { itemId in
                                            await viewModel.toggleFavorite(itemId)
                                        },
                                        onConvert: { itemId in
                                            await viewModel.convertToArticle(itemId)
                                        }
                                    )
                                    .id(group.id)
                                    .onDisappear {
                                        // Mark as read when scrolled past
                                        Task {
                                            await viewModel.onGroupScrolledPast(group.id)
                                        }
                                    }
                                    .onAppear {
                                        // Load more when reaching near end
                                        if group.id == viewModel.newsGroups.last?.id {
                                            Task {
                                                await viewModel.loadMoreGroups()
                                            }
                                        }
                                    }
                                }

                                // Loading indicator at bottom
                                if viewModel.isLoadingMore {
                                    HStack {
                                        Spacer()
                                        ProgressView()
                                            .padding()
                                        Spacer()
                                    }
                                }
                            }
                            .padding(.horizontal, 16)
                            .padding(.vertical, 8)
                        }
                        .refreshable {
                            await viewModel.refresh()
                        }
                    }
                }
                .task {
                    await viewModel.loadNewsGroups()
                }
            }
            .navigationTitle("News")
        }
    }
}

#Preview {
    NewsView()
}
```

**Step 2: Build to verify compilation**

Run: `cd /Users/willem/Development/news_app/client/newsly && xcodebuild -project newsly.xcodeproj -scheme newsly -destination 'platform=iOS Simulator,name=iPhone 15' build`
Expected: BUILD SUCCEEDED

**Step 3: Run app in simulator to test**

Run: `cd /Users/willem/Development/news_app/client/newsly && xcodebuild -project newsly.xcodeproj -scheme newsly -destination 'platform=iOS Simulator,name=iPhone 15' run`
Expected: App launches, news tab shows grouped cards with 5 items each

**Step 4: Commit updated NewsView**

```bash
cd /Users/willem/Development/news_app
git add client/newsly/newsly/Views/NewsView.swift
git commit -m "feat(ios): replace NewsView with grouped news display

- Use NewsGroupViewModel instead of ContentListViewModel
- Display news in groups of 5 with NewsGroupCard
- Auto-mark groups as read when scrolled past
- Replace mark/unlike buttons with favorite/convert
- Add pull-to-refresh support
- Load more groups on scroll

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: Backend - Add Backend Test for Convert Integration

**Files:**
- Create: `app/tests/integration/test_convert_workflow.py`

**Step 1: Write integration test**

Create `app/tests/integration/test_convert_workflow.py`:

```python
"""Integration tests for news-to-article conversion workflow."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content


def test_full_convert_workflow(client: TestClient, db: Session) -> None:
    """Test complete workflow: create news → convert → verify article."""
    # 1. Create news item with article URL
    news = Content(
        url="https://news.ycombinator.com/item?id=99999",
        content_type=ContentType.NEWS.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "article": {
                "url": "https://techblog.example/future-of-ai",
                "title": "The Future of AI",
                "source_domain": "techblog.example"
            },
            "summary": {
                "title": "AI Discussion on HN",
                "overview": "Interesting discussion about AI trends",
                "bullet_points": [
                    {"text": "AI is evolving rapidly"},
                    {"text": "New models are more efficient"}
                ]
            }
        },
    )
    db.add(news)
    db.commit()
    db.refresh(news)

    # 2. Convert news to article
    convert_response = client.post(f"/api/content/{news.id}/convert-to-article")
    assert convert_response.status_code == 200

    convert_data = convert_response.json()
    assert convert_data["status"] == "success"
    assert convert_data["already_exists"] is False
    new_article_id = convert_data["new_content_id"]

    # 3. Verify article was created correctly
    article = db.query(Content).filter(Content.id == new_article_id).first()
    assert article is not None
    assert article.content_type == ContentType.ARTICLE.value
    assert article.url == "https://techblog.example/future-of-ai"
    assert article.title == "The Future of AI"
    assert article.source == "techblog.example"
    assert article.status == ContentStatus.PENDING.value

    # 4. Verify article appears in content list
    list_response = client.get("/api/content/?content_type=article")
    assert list_response.status_code == 200

    articles = list_response.json()["contents"]
    article_ids = [a["id"] for a in articles]
    assert new_article_id in article_ids

    # 5. Try converting same news again - should return existing article
    convert_again = client.post(f"/api/content/{news.id}/convert-to-article")
    assert convert_again.status_code == 200

    convert_again_data = convert_again.json()
    assert convert_again_data["already_exists"] is True
    assert convert_again_data["new_content_id"] == new_article_id


def test_convert_marks_news_as_favorite_interaction(
    client: TestClient, db: Session
) -> None:
    """Test that favoriting news and converting preserves favorite status."""
    # Create news item
    news = Content(
        url="https://news.ycombinator.com/item?id=88888",
        content_type=ContentType.NEWS.value,
        status=ContentStatus.COMPLETED.value,
        content_metadata={
            "article": {"url": "https://example.com/article"}
        },
    )
    db.add(news)
    db.commit()
    db.refresh(news)

    # Favorite the news
    fav_response = client.post(f"/api/content/{news.id}/favorite")
    assert fav_response.status_code == 200
    assert fav_response.json()["is_favorited"] is True

    # Convert to article
    convert_response = client.post(f"/api/content/{news.id}/convert-to-article")
    assert convert_response.status_code == 200

    new_article_id = convert_response.json()["new_content_id"]

    # Verify news is still favorited
    news_detail = client.get(f"/api/content/{news.id}")
    assert news_detail.json()["is_favorited"] is True

    # Article should NOT inherit favorite status (separate content)
    article_detail = client.get(f"/api/content/{new_article_id}")
    assert article_detail.json()["is_favorited"] is False
```

**Step 2: Run integration test**

Run: `. .venv/bin/activate && pytest app/tests/integration/test_convert_workflow.py -v`
Expected: PASS for all integration tests

**Step 3: Commit integration tests**

```bash
cd /Users/willem/Development/news_app
git add app/tests/integration/test_convert_workflow.py
git commit -m "test: add integration tests for convert workflow

- Test full convert workflow from news to article
- Verify duplicate detection works correctly
- Test interaction with favorites
- Ensure converted articles appear in content list

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 8: Documentation and Final Testing

**Files:**
- Create: `docs/library/features/news-grouped-view.md`
- Modify: `client/newsly/CLAUDE.md` (add section about news grouped view)

**Step 1: Create feature documentation**

Create `docs/library/features/news-grouped-view.md`:

```markdown
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
```

**Step 2: Update iOS CLAUDE.md**

Add to `client/newsly/CLAUDE.md` in a new section:

```markdown
### News Grouped View Pattern

The News tab uses a unique grouped display pattern different from Articles and Podcasts:

**Pattern**:
- Groups of exactly 5 news items displayed in cards
- Auto-mark entire group as read when scrolled past (`.onDisappear`)
- Replace individual "mark as read" with group-level actions

**Models**:
- `NewsGroup`: Wraps 5 `ContentSummary` items with group ID and read state
- `groupedByFive()`: Extension method to chunk arrays into groups

**ViewModels**:
- `NewsGroupViewModel`: Specialized for grouped display
  - Tracks `viewedGroupIds` to prevent duplicate marking
  - Uses bulk mark-as-read endpoint for entire groups
  - Handles pagination by loading 25 items (5 groups)

**Views**:
- `NewsGroupCard`: Custom card for 5-item groups
- Actions: Favorite (first item), Convert (to article)

**Critical**: Do NOT use `ContentListViewModel` for news tab - it's for infinite scroll patterns only.
```

**Step 3: Run full test suite**

Backend:
```bash
cd /Users/willem/Development/news_app
. .venv/bin/activate
pytest app/tests/ -v --tb=short
```
Expected: All tests pass

iOS:
```bash
cd /Users/willem/Development/news_app/client/newsly
xcodebuild test -project newsly.xcodeproj -scheme newsly -destination 'platform=iOS Simulator,name=iPhone 15'
```
Expected: All tests pass (or manual testing if no test suite)

**Step 4: Commit documentation**

```bash
cd /Users/willem/Development/news_app
git add docs/library/features/news-grouped-view.md client/newsly/CLAUDE.md
git commit -m "docs: add news grouped view feature documentation

- Document grouped display pattern
- Explain auto-mark behavior
- Add backend endpoint documentation
- Update iOS CLAUDE.md with pattern guidance
- Include testing instructions

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Summary

This plan implements a news grouped view with the following changes:

**Backend**:
- New `POST /api/content/{id}/convert-to-article` endpoint
- Extracts article URLs from news metadata
- Creates article content entries for processing
- De-duplicates existing articles

**iOS**:
- `NewsGroup` model for grouping 5 news items
- `NewsGroupCard` component for grouped display
- `NewsGroupViewModel` for group-level operations
- Updated `NewsView` with scroll-based auto-marking
- Replace mark/unlike buttons with favorite/convert

**Key Features**:
- Groups of exactly 5 news items
- Auto-mark entire group when scrolled past
- Favorite and convert actions on groups
- Smooth animations for marking/removing groups
- Pagination support for loading more groups

**Testing**:
- Comprehensive backend unit and integration tests
- iOS build verification at each step
- Manual testing in simulator

The implementation follows iOS best practices (MVVM), FastAPI patterns (Pydantic v2, dependencies), and includes proper error handling, pagination, and state management.
