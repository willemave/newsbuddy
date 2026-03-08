# News Feed Button Navigation Design

**Date:** 2025-11-01
**Status:** Approved
**Context:** Replace unreliable swipe gestures with button-based navigation for News tab

## Problem Statement

The current News tab uses a swipe-based card stack (similar to Tinder) for navigating through news groups. Users report that the swipe gestures are unreliable and buggy, making it difficult to consume news content efficiently.

**Current Implementation:**
- `CardStackView` with custom `SwipeableCard` component
- Gesture-based dismissal (drag to remove cards)
- Stack visualization (cards layered behind top card)
- Immediate visual feedback via `dismissedGroupIds` state

**Issues:**
- Swipe gestures don't register consistently
- Custom gesture handling adds complexity and bugs
- No clear navigation affordance for users

## Solution Overview

Replace the custom swipe-based card stack with SwiftUI's native `TabView` paging component, adding an explicit "Next" button for navigation.

**Key Changes:**
1. Remove `SwipeableCard` component (eliminates custom gesture code)
2. Create new `PagedCardView` using `TabView(.page)`
3. Add "Next" button below paged view for explicit navigation
4. Keep `NewsGroupCard` unchanged (card content stays the same)

**Benefits:**
- Native SwiftUI paging (reliable, tested, maintained by Apple)
- Explicit navigation control (discoverable, accessible)
- Smooth page transitions with built-in animations
- Simpler codebase (less custom gesture logic)

## Architecture

### Component Hierarchy

```
NewsView (unchanged)
  └── PagedCardView (new, replaces CardStackView)
      ├── TabView(.page)
      │   └── NewsGroupCard (for each visible group)
      └── Next Button (below TabView)
```

### New Component: PagedCardView

```swift
struct PagedCardView: View {
    // Props from parent
    let groups: [NewsGroup]
    let onDismiss: (String) async -> Void
    let onConvert: (Int) async -> Void

    // Local state
    @State private var dismissedGroupIds: Set<String> = []
    @State private var currentIndex: Int = 0

    // Computed
    private var visibleGroups: [NewsGroup] {
        groups.filter { group in
            !group.isRead && !dismissedGroupIds.contains(group.id)
        }
    }
}
```

### State Management

**Local State:**
- `dismissedGroupIds: Set<String>` - Tracks dismissed cards for immediate UI update
- `currentIndex: Int` - Current page position (always reset to 0 when visibleGroups changes)

**Computed State:**
- `visibleGroups: [NewsGroup]` - Filters out read groups and dismissed groups
- Updates automatically when `groups`, `dismissedGroupIds`, or group.isRead changes

**State Flow:**
1. User taps "Next" button
2. Get current card: `visibleGroups[currentIndex]`
3. Add to `dismissedGroupIds` (instant UI update)
4. Call `onDismiss(groupId)` async (backend marks as read)
5. SwiftUI recalculates `visibleGroups` (excluded dismissed card)
6. TabView shows next card (new index 0 of visibleGroups)
7. Reset `currentIndex` to 0

## User Interface

### Layout

```
┌─────────────────────────────────┐
│     Navigation Title: News      │
├─────────────────────────────────┤
│                                 │
│      [NewsGroupCard]            │
│      (Current page)             │
│                                 │
│      ○ ● ○ ○ (page dots)        │
│                                 │
├─────────────────────────────────┤
│    [  Next  →  ]                │ <- Button
├─────────────────────────────────┤
│  ⚫ Articles  🎙 Podcasts ...    │ <- Tab Bar
└─────────────────────────────────┘
```

### Button Design

**Position:** Below TabView, centered, above tab bar

**Visual States:**
- **Normal:** Blue accent color, enabled, label "Next" with arrow icon
- **Last Card:** Label changes to "Done" or button disabled
- **Loading:** Show progress indicator during backend call

**Style Options:**
- Prominent button: Large SF Symbol `arrow.right.circle.fill` + "Next" label
- Or: Capsule button with solid background color

### Page Indicator

- Native TabView page dots (automatic)
- Shows current position and total count
- Updates dynamically as cards dismissed

### Animations

- **Page transition:** Native TabView slide/curl animation
- **Card dismissal:** Instant removal (visibleGroups filter updates)
- **Next card appearance:** Automatic slide-in via TabView
- **Empty state:** Fade transition when last card dismissed

## Data Flow

### Navigation Flow

```
[User taps Next Button]
        ↓
[Get current group: visibleGroups[currentIndex]]
        ↓
[Add groupId to dismissedGroupIds] ← Instant UI update
        ↓
[Call onDismiss(groupId) async] ← Backend update
        ↓
[visibleGroups recalculates] ← Removes dismissed card
        ↓
[TabView shows next card] ← Auto-advance to index 0
        ↓
[Reset currentIndex to 0]
```

### Refresh Flow

```
[User pulls to refresh]
        ↓
[NewsGroupViewModel.refresh()]
        ↓
[Clear dismissedGroupIds in PagedCardView]
        ↓
[Reset currentIndex to 0]
        ↓
[Load fresh groups from API]
        ↓
[visibleGroups recalculates]
        ↓
[TabView shows first card]
```

## Edge Cases

### 1. All Cards Dismissed
**Scenario:** User dismisses all visible groups
**Behavior:** Show "No more news" empty state (reuse existing from CardStackView)
**Implementation:** Check `if visibleGroups.isEmpty` in body

### 2. Refresh While Viewing
**Scenario:** User triggers refresh while on page 3 of 5
**Behavior:** Reset to first card, clear dismissed state
**Implementation:** `.onChange(of: groups.count)` clears dismissedGroupIds and currentIndex

### 3. Backend Delay
**Scenario:** `onDismiss` async call takes 2 seconds
**Behavior:** Card immediately hidden, next card shown, no waiting
**Implementation:** Dismissed state is local (`dismissedGroupIds`), async call runs in background

### 4. Single Card Remaining
**Scenario:** Only one card left in visibleGroups
**Behavior:** Button label changes to "Done" or becomes disabled
**Implementation:** Check `visibleGroups.count == 1` for button state

### 5. Groups Array Updates
**Scenario:** Backend removes or marks groups as read independently
**Behavior:** Clean up dismissed IDs that no longer exist
**Implementation:**
```swift
.onChange(of: groups.count) { oldCount, newCount in
    let currentGroupIds = Set(groups.map { $0.id })
    dismissedGroupIds = dismissedGroupIds.intersection(currentGroupIds)
}
```

### 6. Rapid Button Taps
**Scenario:** User taps "Next" rapidly multiple times
**Behavior:** Only dismiss current card once, ignore subsequent taps until state updates
**Implementation:** Disable button temporarily during dismissal or guard against empty visibleGroups

## Accessibility

### VoiceOver Support
- Button labeled clearly: "Next news group" or "Done reading news"
- Page indicator announces: "Page 2 of 5"
- Cards maintain existing accessibility labels from NewsGroupCard

### Dynamic Type
- Button respects user's text size preferences
- Card content already supports dynamic type

### Reduced Motion
- TabView respects `UIAccessibility.isReduceMotionEnabled`
- Fallback to crossfade instead of slide animation

## Testing Considerations

### Manual Testing
1. Navigate through all cards using Next button
2. Verify page dots update correctly
3. Test refresh clears dismissed state
4. Verify empty state appears after last card
5. Test with single card remaining
6. Verify backend mark-as-read calls are made

### Edge Case Testing
1. Rapid button taps (ensure no crashes)
2. Refresh while on middle card
3. Backend update during navigation
4. VoiceOver navigation
5. Landscape orientation

## Implementation Notes

### Files to Modify
- **Create:** `client/newsly/newsly/Views/Components/PagedCardView.swift`
- **Modify:** `client/newsly/newsly/Views/NewsView.swift` (replace CardStackView with PagedCardView)
- **Remove:** `client/newsly/newsly/Views/Components/SwipeableCard.swift` (no longer needed)
- **Keep:** `client/newsly/newsly/Views/Components/CardStackView.swift` (keep for reference, remove later)
- **Keep:** `client/newsly/newsly/Views/Components/NewsGroupCard.swift` (unchanged)

### Dependencies
- No new dependencies (uses native SwiftUI TabView)
- Requires iOS 15.0+ (already minimum target)

### Migration Strategy
1. Create PagedCardView alongside existing CardStackView
2. Update NewsView to use PagedCardView
3. Test thoroughly
4. Remove SwipeableCard and old CardStackView

## Alternative Approaches Considered

### 1. Simple Button (Minimal Changes)
**Description:** Add Next button that triggers existing dismiss logic
**Pros:** Quick implementation, keeps all current code
**Cons:** Still uses custom gesture logic, doesn't fix reliability issues
**Decision:** Rejected - doesn't solve root cause

### 2. Button with Custom Animation
**Description:** Next button with custom fade-out animation
**Pros:** More polished dismissal feel
**Cons:** Still requires custom animation code, moderate complexity
**Decision:** Rejected - native TabView provides better animations

### 3. Replace with TabView Paging (Chosen)
**Description:** Rebuild using SwiftUI TabView with page style
**Pros:** Native iOS pattern, reliable, smooth animations, less code
**Cons:** Larger refactor required
**Decision:** Accepted - best long-term solution

## Open Questions

None - design approved and ready for implementation.

## Next Steps

1. Set up git worktree for isolated development
2. Create detailed implementation plan
3. Implement PagedCardView
4. Update NewsView integration
5. Test across devices and iOS versions
6. Remove deprecated SwipeableCard component
