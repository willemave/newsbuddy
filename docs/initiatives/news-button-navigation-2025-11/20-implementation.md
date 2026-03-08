# News Button Navigation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace swipe-based navigation with button-based paging in News tab to fix unreliable gesture handling.

**Architecture:** Replace custom CardStackView/SwipeableCard with native SwiftUI TabView in page mode, add explicit Next button for navigation, maintain existing mark-as-read behavior.

**Tech Stack:** SwiftUI (TabView, State management), iOS 15.0+

**Design Document:** `docs/initiatives/news-button-navigation-2025-11/10-design.md`

---

## Task 1: Create PagedCardView Component

**Goal:** Build new component using TabView for paging with Next button navigation.

**Files:**
- Create: `client/newsly/newsly/Views/Components/PagedCardView.swift`
- Reference: `client/newsly/newsly/Views/Components/CardStackView.swift` (for comparison)
- Reference: `client/newsly/newsly/Views/Components/NewsGroupCard.swift` (reused component)

### Step 1: Create PagedCardView.swift file

**Action:** Create new file with complete implementation.

**File:** `client/newsly/newsly/Views/Components/PagedCardView.swift`

**Code:**

```swift
//
//  PagedCardView.swift
//  newsly
//
//  Button-based navigation using TabView paging (replaces swipe gestures)
//

import SwiftUI

struct PagedCardView: View {
    let groups: [NewsGroup]
    let onDismiss: (String) async -> Void
    let onConvert: (Int) async -> Void

    // Track dismissed group IDs for immediate visual feedback
    @State private var dismissedGroupIds: Set<String> = []

    // Current page index (always reset to 0 when visibleGroups changes)
    @State private var currentIndex: Int = 0

    // Button state during async operations
    @State private var isProcessing: Bool = false

    // Visible groups = not read AND not dismissed
    private var visibleGroups: [NewsGroup] {
        groups.filter { group in
            !group.isRead && !dismissedGroupIds.contains(group.id)
        }
    }

    var body: some View {
        GeometryReader { geometry in
            VStack(spacing: 0) {
                if visibleGroups.isEmpty {
                    // Empty state - all cards dismissed
                    VStack(spacing: 16) {
                        Image(systemName: "newspaper")
                            .font(.largeTitle)
                            .foregroundColor(.secondary)
                        Text("No more news")
                            .font(.title3)
                            .foregroundColor(.secondary)
                        Text("Pull to refresh")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    // Paged card view
                    TabView(selection: $currentIndex) {
                        ForEach(Array(visibleGroups.enumerated()), id: \.element.id) { index, group in
                            NewsGroupCard(
                                group: group,
                                onConvert: onConvert
                            )
                            .tag(index)
                            .padding(.horizontal, 16)
                        }
                    }
                    .tabViewStyle(.page)
                    .indexViewStyle(.page(backgroundDisplayMode: .always))
                    .frame(maxHeight: geometry.size.height - 100)

                    // Next/Done button
                    Button(action: {
                        handleNextTapped()
                    }) {
                        HStack(spacing: 8) {
                            if isProcessing {
                                ProgressView()
                                    .progressViewStyle(CircularProgressViewStyle(tint: .white))
                            } else {
                                Text(visibleGroups.count == 1 ? "Done" : "Next")
                                    .fontWeight(.semibold)
                                Image(systemName: "arrow.right.circle.fill")
                                    .font(.title3)
                            }
                        }
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 16)
                        .background(
                            RoundedRectangle(cornerRadius: 12)
                                .fill(Color.accentColor)
                        )
                    }
                    .disabled(isProcessing || visibleGroups.isEmpty)
                    .opacity(isProcessing ? 0.6 : 1.0)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 8)
                }
            }
        }
        .animation(.easeInOut(duration: 0.2), value: visibleGroups.count)
        .onChange(of: groups.count) { oldCount, newCount in
            // Clean up dismissed IDs that are no longer in the groups array
            if newCount < oldCount {
                let currentGroupIds = Set(groups.map { $0.id })
                dismissedGroupIds = dismissedGroupIds.intersection(currentGroupIds)
            }

            // On refresh (count goes to 0 or significantly changes), clear dismissed set
            if newCount == 0 || abs(newCount - oldCount) > 10 {
                dismissedGroupIds.removeAll()
                currentIndex = 0
            }
        }
        .onChange(of: visibleGroups.count) { _, _ in
            // Reset to first page when visible groups change
            if !visibleGroups.isEmpty && currentIndex >= visibleGroups.count {
                currentIndex = 0
            }
        }
    }

    private func handleNextTapped() {
        // Guard: ensure we have visible groups
        guard !visibleGroups.isEmpty, currentIndex < visibleGroups.count else {
            return
        }

        // Prevent rapid taps
        guard !isProcessing else { return }

        let dismissedGroup = visibleGroups[currentIndex]

        // Mark as processing
        isProcessing = true

        // Mark as dismissed immediately (synchronous - instant visual feedback)
        dismissedGroupIds.insert(dismissedGroup.id)

        // Reset to first page (will show next card since current is now filtered out)
        currentIndex = 0

        // Call async operations in background (backend update)
        Task {
            await onDismiss(dismissedGroup.id)
            // Reset processing state
            isProcessing = false
        }
    }
}
```

**Verification:** File should be created at `client/newsly/newsly/Views/Components/PagedCardView.swift`

**Why this approach:**
- TabView(.page) provides native, reliable paging (no custom gestures)
- State management mirrors existing CardStackView (dismissedGroupIds pattern)
- Button provides explicit, discoverable navigation
- Loading state prevents rapid taps
- Auto-resets to index 0 when visibleGroups changes

### Step 2: Add to Xcode project

**Action:** Open Xcode and add the new file to the project.

**Instructions:**
1. Open `client/newsly/newsly.xcodeproj` in Xcode
2. Right-click on `Views/Components` folder in Project Navigator
3. Select "Add Files to newsly..."
4. Navigate to and select `PagedCardView.swift`
5. Ensure "Copy items if needed" is unchecked (file already in place)
6. Ensure "newsly" target is checked
7. Click "Add"

**Verification:** PagedCardView.swift appears in Project Navigator under `Views/Components/`

### Step 3: Verify file compiles

**Action:** Build the project to ensure no syntax errors.

**Instructions:**
1. In Xcode, select Product → Build (⌘B)
2. Check for compilation errors in Issue Navigator

**Expected Output:** Build succeeds with no errors

**If errors occur:** Review syntax, check import statements, verify NewsGroup and NewsGroupCard are accessible

### Step 4: Commit PagedCardView

**Action:** Commit the new component.

```bash
git add client/newsly/newsly/Views/Components/PagedCardView.swift
git commit -m "feat(ios): add PagedCardView with button navigation

Replace swipe-based card stack with TabView paging and Next button.
Maintains existing dismiss/mark-as-read behavior with more reliable UI.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

**Verification:** Run `git log -1 --oneline` to confirm commit

---

## Task 2: Update NewsView to Use PagedCardView

**Goal:** Replace CardStackView with PagedCardView in News tab.

**Files:**
- Modify: `client/newsly/newsly/Views/NewsView.swift`

### Step 1: Update NewsView.swift

**Action:** Replace CardStackView with PagedCardView.

**File:** `client/newsly/newsly/Views/NewsView.swift`

**Find:**
```swift
                    } else {
                        CardStackView(
                            groups: viewModel.newsGroups,
                            onDismiss: { groupId in
                                await viewModel.markGroupAsRead(groupId)
                                await viewModel.preloadNextGroups()
                            },
                            onConvert: { itemId in
                                await viewModel.convertToArticle(itemId)
                            }
                        )
                        .refreshable {
                            await viewModel.refresh()
                        }
                    }
```

**Replace with:**
```swift
                    } else {
                        PagedCardView(
                            groups: viewModel.newsGroups,
                            onDismiss: { groupId in
                                await viewModel.markGroupAsRead(groupId)
                                await viewModel.preloadNextGroups()
                            },
                            onConvert: { itemId in
                                await viewModel.convertToArticle(itemId)
                            }
                        )
                        .refreshable {
                            await viewModel.refresh()
                        }
                    }
```

**Complete updated NewsView.swift:**

```swift
//
//  NewsView.swift
//  newsly
//
//  Created by Assistant on 9/20/25.
//  Updated by Assistant on 10/12/25 for grouped display
//  Updated by Assistant on 11/01/25 for button navigation
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
                        PagedCardView(
                            groups: viewModel.newsGroups,
                            onDismiss: { groupId in
                                await viewModel.markGroupAsRead(groupId)
                                await viewModel.preloadNextGroups()
                            },
                            onConvert: { itemId in
                                await viewModel.convertToArticle(itemId)
                            }
                        )
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

**Verification:** Only change should be `CardStackView` → `PagedCardView`

### Step 2: Build and verify

**Action:** Build project to ensure changes compile.

**Instructions:**
1. In Xcode, select Product → Build (⌘B)
2. Check for compilation errors

**Expected Output:** Build succeeds

### Step 3: Commit NewsView update

**Action:** Commit the integration.

```bash
git add client/newsly/newsly/Views/NewsView.swift
git commit -m "feat(ios): switch NewsView to PagedCardView

Replace CardStackView with new button-based navigation.
All behavior (mark-as-read, convert, preload) unchanged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

**Verification:** Run `git log -1 --oneline` to confirm commit

---

## Task 3: Manual Testing in Simulator

**Goal:** Verify button navigation works correctly across all scenarios.

**Prerequisites:**
- Xcode project built successfully
- Backend server running with news data

### Step 1: Start backend server

**Action:** Ensure backend is running with news content.

**Instructions:**
```bash
# In terminal, from project root
cd /Users/willem/Development/news_app
source .venv/bin/activate
./scripts/start_server.sh
```

**Verification:** Server starts on http://localhost:8000

**Alternative:** If server already running, skip this step.

### Step 2: Run iOS app in simulator

**Action:** Launch app in Xcode simulator.

**Instructions:**
1. In Xcode, select a simulator (e.g., iPhone 15 Pro)
2. Click Run button (▶) or press ⌘R
3. Wait for app to build and launch in simulator

**Expected:** App launches successfully, no crashes

### Step 3: Navigate to News tab

**Action:** Open News tab and verify initial state.

**Instructions:**
1. Tap "News" tab at bottom of screen
2. Observe initial view

**Expected Results:**
- ✓ Single news card visible (not stacked cards)
- ✓ Page dots appear below card (e.g., "● ○ ○ ○ ○")
- ✓ "Next" button visible at bottom (blue, enabled)
- ✓ No swipe gesture indicators

**If fails:** Check that PagedCardView is being used in NewsView.swift

### Step 4: Test Next button navigation

**Action:** Tap Next button multiple times.

**Instructions:**
1. Note the current card headline
2. Tap "Next" button
3. Observe transition
4. Repeat 3-4 times

**Expected Results:**
- ✓ Card slides away with smooth animation
- ✓ Next card slides in immediately
- ✓ Page dots update (first dot becomes unfilled, second becomes filled)
- ✓ No lag or stutter
- ✓ Button shows loading indicator briefly during tap

**If fails:**
- Check console for errors
- Verify `handleNextTapped()` logic in PagedCardView
- Check that `onDismiss` callback is being called

### Step 5: Test last card behavior

**Action:** Navigate to the last card.

**Instructions:**
1. Continue tapping Next until only one card remains
2. Observe button label

**Expected Results:**
- ✓ Button changes from "Next" to "Done" on last card
- ✓ Button still enabled and tappable

**If fails:** Check button label logic: `visibleGroups.count == 1 ? "Done" : "Next"`

### Step 6: Test empty state

**Action:** Dismiss all cards.

**Instructions:**
1. Tap "Done" on the last card
2. Observe empty state

**Expected Results:**
- ✓ Card disappears
- ✓ Empty state appears: newspaper icon, "No more news", "Pull to refresh"
- ✓ Button is hidden or disabled

**If fails:** Check `if visibleGroups.isEmpty` condition in PagedCardView body

### Step 7: Test pull-to-refresh

**Action:** Refresh to reload cards.

**Instructions:**
1. From empty state, pull down on screen
2. Release to trigger refresh
3. Observe behavior

**Expected Results:**
- ✓ Loading indicator appears briefly
- ✓ Cards reload from beginning
- ✓ First card is shown
- ✓ Page dots reset
- ✓ "Next" button reappears

**If fails:** Check `.refreshable` closure in NewsView and `.onChange(of: groups.count)` in PagedCardView

### Step 8: Test mark-as-read behavior

**Action:** Verify dismissed cards are marked as read.

**Instructions:**
1. Navigate through 3-4 cards using Next button
2. Switch to "Recently Read" tab
3. Check if dismissed news items appear

**Expected Results:**
- ✓ Dismissed news groups appear in Recently Read
- ✓ Timestamp shows recent dismissal

**If fails:**
- Check that `onDismiss(groupId)` is calling `viewModel.markGroupAsRead(groupId)`
- Verify backend `/api/content/bulk-mark-read` endpoint is working

### Step 9: Test rapid button taps

**Action:** Attempt to break navigation with rapid taps.

**Instructions:**
1. From first card, rapidly tap Next button 5-6 times quickly
2. Observe behavior

**Expected Results:**
- ✓ Only one card dismissed per tap (no double-dismissals)
- ✓ Button becomes disabled during processing
- ✓ No crashes or errors
- ✓ Animation completes smoothly

**If fails:**
- Check `isProcessing` state guard in `handleNextTapped()`
- Verify button `.disabled(isProcessing)` modifier

### Step 10: Test landscape orientation

**Action:** Rotate simulator to landscape.

**Instructions:**
1. In simulator, select Device → Rotate Left (⌘←)
2. Observe layout
3. Tap Next button

**Expected Results:**
- ✓ Card resizes appropriately
- ✓ Button remains visible and accessible
- ✓ Navigation still works smoothly

**If fails:** Adjust GeometryReader frame calculations if needed

### Step 11: Document test results

**Action:** Create test summary document.

**File:** Create `docs/initiatives/news-button-navigation-2025-11/30-test-results.md`

**Template:**
```markdown
# News Button Navigation - Test Results

**Date:** 2025-11-01
**Tester:** [Your name]
**Device:** [Simulator model]
**iOS Version:** [Version]

## Test Scenarios

| Scenario | Result | Notes |
|----------|--------|-------|
| Initial view loads | PASS/FAIL | |
| Next button navigates | PASS/FAIL | |
| Page dots update | PASS/FAIL | |
| Last card shows "Done" | PASS/FAIL | |
| Empty state appears | PASS/FAIL | |
| Pull-to-refresh works | PASS/FAIL | |
| Mark-as-read persists | PASS/FAIL | |
| Rapid taps handled | PASS/FAIL | |
| Landscape orientation | PASS/FAIL | |

## Issues Found

[List any bugs or unexpected behavior]

## Overall Assessment

PASS / FAIL (with issues) / FAIL

## Next Steps

[Any follow-up work needed]
```

**Verification:** Document saved to `docs/initiatives/news-button-navigation-2025-11/`

---

## Task 4: Update iOS App Documentation

**Goal:** Document the navigation change in iOS app architecture notes.

**Files:**
- Modify: `client/newsly/CLAUDE.md`

### Step 1: Update CLAUDE.md

**Action:** Add note about button navigation in News tab.

**File:** `client/newsly/CLAUDE.md`

**Find:** (Under "### News Grouped View Pattern" section)

**Add after the pattern description:**

```markdown
**Navigation Pattern (Updated 2025-11-01):**
- Uses `PagedCardView` with native TabView(.page) for reliable paging
- Button-based navigation (Next/Done button) instead of swipe gestures
- Maintains auto-mark-as-read behavior when cards dismissed
- More discoverable and accessible than swipe-only navigation
```

**Complete addition:**
```markdown
### News Grouped View Pattern

The News tab uses a unique grouped display pattern different from Articles and Podcasts:

**Pattern**:
- Groups of exactly 5 news items displayed in cards
- Auto-mark entire group as read when scrolled past (`.onDisappear`)
- Replace individual "mark as read" with group-level actions

**Navigation Pattern (Updated 2025-11-01):**
- Uses `PagedCardView` with native TabView(.page) for reliable paging
- Button-based navigation (Next/Done button) instead of swipe gestures
- Maintains auto-mark-as-read behavior when cards dismissed
- More discoverable and accessible than swipe-only navigation

**Models**:
- `NewsGroup`: Wraps 5 `ContentSummary` items with group ID and read state
- `groupedByFive()`: Extension method to chunk arrays into groups
```

### Step 2: Commit documentation update

**Action:** Commit the documentation change.

```bash
git add client/newsly/CLAUDE.md
git commit -m "docs(ios): update news navigation documentation

Document switch from swipe to button-based navigation.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

**Verification:** Run `git log -1 --oneline`

---

## Task 5: Cleanup (Optional)

**Goal:** Remove deprecated SwipeableCard component if no longer used.

**Files:**
- Check: `client/newsly/newsly/Views/Components/CardStackView.swift`
- Potentially remove: `client/newsly/newsly/Views/Components/SwipeableCard.swift`

### Step 1: Verify SwipeableCard is unused

**Action:** Search codebase for SwipeableCard references.

**Instructions:**
```bash
# From project root
cd client/newsly/newsly
grep -r "SwipeableCard" . --include="*.swift" | grep -v "SwipeableCard.swift"
```

**Expected Output:**
- Should only show usage in `CardStackView.swift`
- If shows other usages, DO NOT remove (skip to Step 4)

### Step 2: Verify CardStackView is unused

**Action:** Search codebase for CardStackView references.

**Instructions:**
```bash
grep -r "CardStackView" . --include="*.swift" | grep -v "CardStackView.swift"
```

**Expected Output:**
- Should show no usages (we replaced it with PagedCardView)
- If shows usages, DO NOT remove (skip to Step 4)

### Step 3: Remove deprecated components (if safe)

**Action:** Remove SwipeableCard and CardStackView if truly unused.

**Instructions:**
1. In Xcode Project Navigator, locate:
   - `Views/Components/SwipeableCard.swift`
   - `Views/Components/CardStackView.swift`
2. Right-click each file → Delete
3. Choose "Move to Trash"

**Alternative (safer):** Keep files but add deprecation comments:

Add to top of both files:
```swift
// DEPRECATED (2025-11-01): Replaced by PagedCardView
// Kept for reference only - do not use in new code
```

### Step 4: Document decision

**Action:** Add note about cleanup decision.

**If removed:**
```bash
git rm client/newsly/newsly/Views/Components/SwipeableCard.swift
git rm client/newsly/newsly/Views/Components/CardStackView.swift
git commit -m "refactor(ios): remove deprecated swipe components

SwipeableCard and CardStackView replaced by PagedCardView.
No longer used in codebase.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

**If kept:**
```bash
git add client/newsly/newsly/Views/Components/SwipeableCard.swift
git add client/newsly/newsly/Views/Components/CardStackView.swift
git commit -m "docs(ios): deprecate swipe components

Mark SwipeableCard and CardStackView as deprecated.
Kept for reference only.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Success Criteria

**Feature is complete when:**

1. ✓ PagedCardView component created and compiles
2. ✓ NewsView uses PagedCardView instead of CardStackView
3. ✓ All manual tests pass in simulator
4. ✓ No crashes or console errors during normal usage
5. ✓ Button navigation feels responsive and reliable
6. ✓ Mark-as-read behavior works correctly
7. ✓ Pull-to-refresh resets state properly
8. ✓ Documentation updated
9. ✓ All changes committed with proper messages

## Known Limitations

- No swipe gesture fallback (button-only navigation)
- No backward navigation (forward-only flow)
- iOS simulator testing only (should test on real device before release)

## Future Enhancements

Potential improvements for later:

1. **Add Previous button:** Allow reviewing dismissed cards
2. **Keyboard shortcuts:** Arrow keys for navigation on iPad
3. **Haptic feedback:** Subtle haptic on button tap
4. **Animation options:** User preference for transition style
5. **Undo dismissal:** Shake to undo last dismissal

## Troubleshooting

**Issue:** Cards don't advance when Next tapped
- Check: `handleNextTapped()` is being called
- Check: `dismissedGroupIds` is being updated
- Check: `visibleGroups` computed property recalculates
- Check: Console for async errors

**Issue:** Button stays in loading state
- Check: `isProcessing = false` is called after `onDismiss`
- Check: Task completion in `handleNextTapped()`

**Issue:** Page dots don't update
- Check: `currentIndex` is bound to TabView selection
- Check: `.indexViewStyle(.page)` is applied

**Issue:** Empty state doesn't appear
- Check: `if visibleGroups.isEmpty` condition
- Check: All groups have `isRead = true` or in `dismissedGroupIds`

---

## Execution Notes

**Estimated Time:** 45-60 minutes
- Task 1 (Create component): 15 min
- Task 2 (Update NewsView): 5 min
- Task 3 (Manual testing): 20-30 min
- Task 4 (Documentation): 5 min
- Task 5 (Cleanup): 5 min

**Dependencies:**
- Xcode installed and configured
- Backend server running with news data
- iOS simulator available

**Risk Assessment:** LOW
- No breaking API changes
- No database migrations
- Isolated to News tab UI only
- Easy rollback (restore CardStackView usage)
