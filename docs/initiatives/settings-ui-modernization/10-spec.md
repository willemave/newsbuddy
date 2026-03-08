# Settings UI Modernization Spec

> Modern, utilitarian design inspired by X (Twitter) and Linear, with iOS 26 Liquid Glass where appropriate.

## Design Direction

### Aesthetic: **Refined Utilitarian**

Inspired by **X** and **Linear**:
- **X**: Clean list rows, subtle separators, consistent rhythm, avatar-led layouts, timestamp hierarchy
- **Linear**: Crisp borders (not shadows), status chips, monospace accents, keyboard-first density, dark-mode-forward

**Key principles:**
1. **Information density without clutter** — show more, decorate less
2. **Hierarchy through typography** — not color or weight alone
3. **Status at a glance** — chips/badges, not prose
4. **Subtle dividers** — hairline borders, not heavy separators
5. **Glass accents** — interactive elements only, not surfaces

### Color Palette

```swift
// Semantic colors (extend in Assets or code)
extension Color {
    static let surfacePrimary = Color(.systemBackground)
    static let surfaceSecondary = Color(.secondarySystemBackground)
    static let surfaceTertiary = Color(.tertiarySystemBackground)

    static let textPrimary = Color(.label)
    static let textSecondary = Color(.secondaryLabel)
    static let textTertiary = Color(.tertiaryLabel)

    static let borderSubtle = Color(.separator)
    static let borderStrong = Color(.opaqueSeparator)

    // Status colors (Linear-style muted)
    static let statusActive = Color.green.opacity(0.85)
    static let statusInactive = Color(.tertiaryLabel)
    static let statusDestructive = Color.red.opacity(0.85)
}
```

### Typography Scale

```swift
// Consistent type scale
extension Font {
    static let listTitle = Font.body.weight(.medium)
    static let listSubtitle = Font.subheadline
    static let listCaption = Font.caption
    static let listMono = Font.system(.caption, design: .monospaced)

    static let sectionHeader = Font.footnote.weight(.semibold)
    static let chipLabel = Font.caption2.weight(.medium)
}
```

---

## Component Patterns

### 1. List Row (Standard)

**Pattern**: Icon-led row with title, subtitle, and trailing accessory.

```
┌─────────────────────────────────────────────────┐
│ [icon]  Title                        [accessory]│
│         Subtitle / description                  │
└─────────────────────────────────────────────────┘
```

**SwiftUI implementation:**

```swift
struct SettingsRow<Accessory: View>: View {
    let icon: String
    let iconColor: Color
    let title: String
    var subtitle: String? = nil
    @ViewBuilder var accessory: () -> Accessory

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 17, weight: .medium))
                .foregroundStyle(iconColor)
                .frame(width: 28, height: 28)

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.listTitle)
                    .foregroundStyle(.textPrimary)

                if let subtitle {
                    Text(subtitle)
                        .font(.listCaption)
                        .foregroundStyle(.textTertiary)
                        .lineLimit(1)
                }
            }

            Spacer(minLength: 8)

            accessory()
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 16)
        .contentShape(Rectangle())
    }
}
```

### 2. Source Row (Feed/Podcast)

**Pattern**: Compact row with name, URL, and status chip.

```
┌─────────────────────────────────────────────────┐
│ Source Name                          [●] Active │
│ source.example.com                      [chevron]│
└─────────────────────────────────────────────────┘
```

**Linear-style status chip:**

```swift
struct StatusChip: View {
    let isActive: Bool

    var body: some View {
        HStack(spacing: 4) {
            Circle()
                .fill(isActive ? Color.statusActive : Color.statusInactive)
                .frame(width: 6, height: 6)

            Text(isActive ? "Active" : "Inactive")
                .font(.chipLabel)
                .foregroundStyle(isActive ? .textPrimary : .textTertiary)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(
            RoundedRectangle(cornerRadius: 6)
                .fill(Color.surfaceSecondary)
        )
    }
}
```

**Full source row:**

```swift
struct SourceRow: View {
    let name: String
    let url: String?
    let type: String
    let isActive: Bool

    var body: some View {
        HStack(spacing: 12) {
            // Type icon
            SourceTypeIcon(type: type)

            // Content
            VStack(alignment: .leading, spacing: 2) {
                Text(name)
                    .font(.listTitle)
                    .foregroundStyle(.textPrimary)
                    .lineLimit(1)

                if let url {
                    Text(formattedURL(url))
                        .font(.listMono)
                        .foregroundStyle(.textTertiary)
                        .lineLimit(1)
                }
            }

            Spacer(minLength: 8)

            // Status + chevron
            HStack(spacing: 8) {
                StatusChip(isActive: isActive)

                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.textTertiary)
            }
        }
        .padding(.vertical, 12)
        .padding(.horizontal, 16)
        .contentShape(Rectangle())
    }

    private func formattedURL(_ urlString: String) -> String {
        guard let url = URL(string: urlString), let host = url.host else {
            return urlString
        }
        return host.replacingOccurrences(of: "www.", with: "")
    }
}
```

### 3. Section Header

**Pattern**: Uppercase label with optional trailing action (Linear-style).

```swift
struct SectionHeader: View {
    let title: String
    var action: (() -> Void)? = nil
    var actionLabel: String? = nil

    var body: some View {
        HStack {
            Text(title.uppercased())
                .font(.sectionHeader)
                .foregroundStyle(.textTertiary)
                .tracking(0.5)

            Spacer()

            if let action, let actionLabel {
                Button(action: action) {
                    Text(actionLabel)
                        .font(.caption)
                        .foregroundStyle(.accentColor)
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 24)
        .padding(.bottom, 8)
    }
}
```

### 4. Empty State

**Pattern**: Centered icon + title + subtitle + optional action.

```swift
struct EmptyStateView: View {
    let icon: String
    let title: String
    let subtitle: String
    var actionTitle: String? = nil
    var action: (() -> Void)? = nil

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: icon)
                .font(.system(size: 40, weight: .light))
                .foregroundStyle(.textTertiary)

            VStack(spacing: 4) {
                Text(title)
                    .font(.headline)
                    .foregroundStyle(.textPrimary)

                Text(subtitle)
                    .font(.subheadline)
                    .foregroundStyle(.textSecondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 280)
            }

            if let actionTitle, let action {
                Button(action: action) {
                    Text(actionTitle)
                        .font(.subheadline.weight(.medium))
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.surfacePrimary)
    }
}
```

### 5. Glass Action Bar (iOS 26+)

**Pattern**: Bottom action bar with Liquid Glass.

```swift
struct GlassActionBar: View {
    let primaryLabel: String
    let primaryAction: () -> Void

    var body: some View {
        if #available(iOS 26, *) {
            HStack {
                Spacer()
                Button(primaryLabel, action: primaryAction)
                    .buttonStyle(.glassProminent)
                Spacer()
            }
            .padding(.vertical, 12)
            .padding(.horizontal, 16)
        } else {
            HStack {
                Spacer()
                Button(primaryLabel, action: primaryAction)
                    .buttonStyle(.borderedProminent)
                Spacer()
            }
            .padding(.vertical, 12)
            .padding(.horizontal, 16)
            .background(.ultraThinMaterial)
        }
    }
}
```

---

## View-by-View Redesign

### SettingsView

**Changes:**
- Replace `Form` with custom `ScrollView` + sections for more control
- Use `SettingsRow` component for consistent spacing
- Add section dividers (hairline)
- Remove emoji from section headers

**Structure:**

```swift
ScrollView {
    VStack(spacing: 0) {
        // Account section
        SectionHeader(title: "Account")
        AccountCard(user: user)
        SectionDivider()

        // Display section
        SectionHeader(title: "Display")
        SettingsToggleRow(...)
        SettingsToggleRow(...)
        SectionDivider()

        // Library section
        SectionHeader(title: "Library")
        SettingsRow(icon: "star", title: "Favorites") {
            NavigationChevron()
        }
        SectionDivider()

        // Sources section
        SectionHeader(title: "Sources")
        SettingsRow(icon: "list.bullet.rectangle", title: "Feed Sources") { ... }
        SettingsRow(icon: "waveform", title: "Podcast Sources") { ... }
        SectionDivider()

        // Actions section
        SectionHeader(title: "Actions")
        SettingsRow(icon: "checkmark.circle", title: "Mark All As Read") { ... }

        #if DEBUG
        SectionDivider()
        SectionHeader(title: "Debug")
        SettingsRow(icon: "ladybug", title: "Debug Menu") { ... }
        #endif
    }
}
.background(Color.surfacePrimary)
```

### FeedSourcesView / PodcastSourcesView

**Changes:**
- Replace `List` with `ScrollView` + custom rows
- Use `SourceRow` component
- Add floating "+" button with Liquid Glass (iOS 26) or bordered style
- Improve add sheet with better field grouping

**Structure:**

```swift
ZStack(alignment: .bottomTrailing) {
    ScrollView {
        LazyVStack(spacing: 0) {
            if viewModel.isLoading && viewModel.configs.isEmpty {
                LoadingPlaceholder()
            } else if viewModel.configs.isEmpty {
                EmptyStateView(
                    icon: "antenna.radiowaves.left.and.right",
                    title: "No Sources",
                    subtitle: "Add feeds to start receiving content",
                    actionTitle: "Add Source",
                    action: { showAddSheet = true }
                )
            } else {
                ForEach(viewModel.configs) { config in
                    SourceRow(
                        name: config.displayName ?? config.feedURL ?? "Feed",
                        url: config.feedURL,
                        type: config.scraperType,
                        isActive: config.isActive
                    )
                    .onTapGesture { selectedConfig = config }

                    Divider()
                        .padding(.leading, 56) // Align with text start
                }
            }
        }
    }
    .refreshable { await viewModel.loadConfigs() }

    // Floating add button
    AddButton { showAddSheet = true }
        .padding(16)
}
.navigationTitle("Feed Sources")
```

**Floating add button:**

```swift
struct AddButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "plus")
                .font(.system(size: 20, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 52, height: 52)
        }
        .if(available: iOS 26) { view in
            view.buttonStyle(.glassProminent)
        } else: { view in
            view.background(Color.accentColor, in: Circle())
                .shadow(color: .black.opacity(0.15), radius: 8, y: 4)
        }
    }
}
```

### FavoritesView

**Changes:**
- Keep `List` for swipe actions (required)
- Simplify row styling
- Improve empty state
- Remove redundant ZStack

**Structure:**

```swift
var body: some View {
    Group {
        if viewModel.isLoading && viewModel.contents.isEmpty {
            LoadingView()
        } else if viewModel.contents.isEmpty {
            EmptyStateView(
                icon: "star",
                title: "No Favorites",
                subtitle: "Swipe right on content to save it here"
            )
        } else {
            contentList
        }
    }
    .navigationTitle("Favorites")
    .toolbar { ... }
}

private var contentList: some View {
    List {
        ForEach(viewModel.contents) { content in
            NavigationLink(value: content) {
                FavoriteRow(content: content)
            }
            .swipeActions(edge: .leading) { ... }
            .swipeActions(edge: .trailing) { ... }
        }
    }
    .listStyle(.plain)
    .refreshable { ... }
}
```

### FeedDetailView

**Changes:**
- Replace `Form` with cleaner card-based layout
- Use text fields with labels above (Linear-style)
- Improve delete button styling
- Better save feedback

**Structure:**

```swift
ScrollView {
    VStack(spacing: 24) {
        // Info card
        VStack(alignment: .leading, spacing: 16) {
            SectionHeader(title: "Information")

            LabeledContent("Type") {
                Text(config.scraperType.capitalized)
                    .font(.listMono)
            }
        }
        .padding()
        .background(Color.surfaceSecondary, in: RoundedRectangle(cornerRadius: 12))

        // Settings card
        VStack(alignment: .leading, spacing: 16) {
            SectionHeader(title: "Settings")

            Toggle("Active", isOn: $isActive)

            LabeledTextField("Display Name", text: $displayName)

            LabeledTextField("Feed URL", text: $feedURL)
                .keyboardType(.URL)

            if config.scraperType == "podcast_rss" {
                LabeledTextField("Episode Limit", text: $limit)
                    .keyboardType(.numberPad)
            }
        }
        .padding()
        .background(Color.surfaceSecondary, in: RoundedRectangle(cornerRadius: 12))

        // Delete button
        Button(role: .destructive) {
            showingDeleteAlert = true
        } label: {
            Label("Delete Source", systemImage: "trash")
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.bordered)
        .tint(.red)
    }
    .padding()
}
```

---

## Liquid Glass Usage

### Where to use (iOS 26+)

| Element | Glass Style | Rationale |
|---------|-------------|-----------|
| Floating action button | `.glassProminent` | Primary action, needs prominence |
| Bottom action bar | `.glass` | Anchored UI, interactive |
| Modal sheet grabber | `.glassEffect()` | Subtle, interactive |
| Toolbar buttons | `.buttonStyle(.glass)` | Secondary actions |

### Where NOT to use

- List backgrounds (too heavy)
- Section headers (not interactive)
- Status chips (too small, use solid color)
- Card surfaces (use solid/translucent materials)

### Availability Handling

```swift
extension View {
    @ViewBuilder
    func glassBackgroundIfAvailable() -> some View {
        if #available(iOS 26, *) {
            self.glassEffect(.regular.interactive(), in: .rect(cornerRadius: 12))
        } else {
            self.background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
        }
    }
}
```

---

## Implementation Order

1. **Create shared components** (`SettingsRow`, `SourceRow`, `StatusChip`, `SectionHeader`, `EmptyStateView`)
2. **Update SettingsView** — replace Form with ScrollView, use new components
3. **Update FeedSourcesView** — new row design, floating button
4. **Update PodcastSourcesView** — mirror FeedSourcesView
5. **Update FavoritesView** — simplify structure, improve empty state
6. **Update FeedDetailView** — card-based form layout
7. **Add Liquid Glass** — iOS 26 progressive enhancement

---

## Acceptance Criteria

- [ ] Consistent 16px horizontal padding across all screens
- [ ] Consistent 12px vertical row padding
- [ ] Section headers use uppercase + tracking
- [ ] Status chips show Active/Inactive state clearly
- [ ] Empty states have icon + title + subtitle
- [ ] Source URLs display as hostname only (no scheme, no www)
- [ ] Floating add button on source lists
- [ ] iOS 26+ uses Liquid Glass for primary actions
- [ ] iOS 25 and below has graceful fallback
- [ ] All existing functionality preserved
- [ ] VoiceOver labels intact

---

## File Structure

```
Views/
  Settings/
    SettingsView.swift
    SettingsComponents.swift     // SettingsRow, SettingsToggleRow
  Sources/
    FeedSourcesView.swift
    PodcastSourcesView.swift
    SourceRow.swift
    SourceDetailSheet.swift      // Renamed from FeedDetailView
  Library/
    FavoritesView.swift
    FavoriteRow.swift
  Shared/
    SectionHeader.swift
    StatusChip.swift
    EmptyStateView.swift
    GlassActionBar.swift
    AddButton.swift
```
