//
//  KnowledgeView.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import SwiftUI

struct KnowledgeView: View {
    let onSelectSession: ((ChatSessionRoute) -> Void)?
    let onSelectContent: ((ContentDetailRoute) -> Void)?

    @StateObject private var viewModel = ChatSessionsViewModel()
    @StateObject private var discoveryViewModel = DiscoveryViewModel()
    @ObservedObject private var settings = AppSettings.shared
    @State private var showingNewChat = false
    @State private var selectedProvider: ChatModelProvider = .anthropic
    @State private var pendingNavigationRoute: ChatSessionRoute?
    @State private var selectedTab: KnowledgeTab = .chats
    @State private var chatSearchText = ""

    /// Tracks the last time this tab was opened for badge calculation
    @AppStorage("knowledgeTabLastOpenedAt") private var lastOpenedTimestamp: Double = 0
    @AppStorage("discoveryTabLastOpenedAt") private var discoveryLastOpenedTimestamp: Double = 0

    /// Captured threshold for showing "new" items (frozen on appear)
    @State private var newItemThreshold: Date = .distantPast
    @State private var discoveryNewThreshold: Date = .distantPast

    private var contentTextSize: DynamicTypeSize {
        ContentTextSize(index: settings.contentTextSizeIndex).dynamicTypeSize
    }

    private var appTextSize: DynamicTypeSize {
        AppTextSize(index: settings.appTextSizeIndex).dynamicTypeSize
    }

    init(
        onSelectSession: ((ChatSessionRoute) -> Void)? = nil,
        onSelectContent: ((ContentDetailRoute) -> Void)? = nil
    ) {
        self.onSelectSession = onSelectSession
        self.onSelectContent = onSelectContent
    }

    /// Number of new items since last tab open
    var newItemCount: Int {
        guard lastOpenedTimestamp > 0 else { return 0 }
        let threshold = Date(timeIntervalSince1970: lastOpenedTimestamp)
        return viewModel.sessions.filter { session in
            parseDate(session.createdAt) > threshold
        }.count
    }

    /// Check if a session is new (created after last visit)
    private func isNewSession(_ session: ChatSessionSummary) -> Bool {
        guard newItemThreshold != .distantPast else { return false }
        return parseDate(session.createdAt) > newItemThreshold
    }

    var body: some View {
        ZStack {
            VStack(spacing: 0) {
                tabPicker
                contentBody
            }
        }
        .background(Color.surfacePrimary.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            // Capture previous threshold before updating (for "New" indicators)
            if lastOpenedTimestamp > 0 {
                newItemThreshold = Date(timeIntervalSince1970: lastOpenedTimestamp)
            }
            if discoveryLastOpenedTimestamp > 0 {
                discoveryNewThreshold = Date(timeIntervalSince1970: discoveryLastOpenedTimestamp)
            }
            // Mark tab as opened (for badge tracking)
            lastOpenedTimestamp = Date().timeIntervalSince1970
            Task { await loadForSelectedTab() }
            Task { await discoveryViewModel.loadSuggestions() }
        }
        .onChange(of: selectedTab) { _, _ in
            if selectedTab == .discover {
                markDiscoverySeen()
            }
            Task { await loadForSelectedTab() }
        }
        .sheet(isPresented: $showingNewChat, onDismiss: {
            // Navigate after sheet dismisses to avoid conflicts
            if let route = pendingNavigationRoute {
                pendingNavigationRoute = nil
                onSelectSession?(route)
            }
        }) {
            NewChatSheet(
                provider: selectedProvider,
                isPresented: $showingNewChat,
                onCreateSession: { session in
                    viewModel.sessions.insert(session, at: 0)
                    // Queue navigation for after sheet dismisses
                    pendingNavigationRoute = ChatSessionRoute(sessionId: session.id)
                }
            )
            .dynamicTypeSize(appTextSize)
            .presentationDetents([.height(380)])
            .presentationDragIndicator(.hidden)
            .presentationCornerRadius(24)
        }
    }

    @ViewBuilder
    private var contentBody: some View {
        switch selectedTab {
        case .chats:
            chatSessionsBody
                .dynamicTypeSize(appTextSize)
        case .discover:
            KnowledgeDiscoveryView(
                viewModel: discoveryViewModel,
                hasNewSuggestions: hasNewDiscoverySuggestions
            )
            .dynamicTypeSize(contentTextSize)
        case .live:
            KnowledgeLiveView(
                initialRoute: LiveVoiceRoute(
                    sourceSurface: .knowledgeLive,
                    autoConnect: false
                ),
                onOpenChatSession: { chatSessionId in
                    onSelectSession?(
                        ChatSessionRoute(
                            sessionId: chatSessionId,
                            mode: .live
                        )
                    )
                }
            )
            .accessibilityIdentifier("knowledge.live")
        case .favorites:
            FavoritesView(showNavigationTitle: false)
                .dynamicTypeSize(appTextSize)
        }
    }

    private var chatSessions: [ChatSessionSummary] {
        viewModel.sessions.filter { $0.sessionType != "voice_live" }
    }

    @ViewBuilder
    private var chatSessionsBody: some View {
        if viewModel.isLoading && viewModel.sessions.isEmpty {
            LoadingView()
        } else if let error = viewModel.errorMessage, viewModel.sessions.isEmpty {
            ErrorView(message: error) {
                Task { await viewModel.loadSessions() }
            }
        } else if chatSessions.isEmpty {
            emptyStateView
        } else {
            sessionListView
        }
    }

    private var tabPicker: some View {
        Picker("Knowledge Tabs", selection: $selectedTab) {
            ForEach(KnowledgeTab.allCases, id: \.self) { tab in
                Text(tabTitle(for: tab))
                    .accessibilityIdentifier("knowledge.segment.\(tab.rawValue)")
                    .tag(tab)
            }
        }
        .pickerStyle(.segmented)
        .padding(.horizontal, Spacing.screenHorizontal)
        .padding(.top, 8)
        .padding(.bottom, 4)
        .accessibilityIdentifier("knowledge.tab_picker")
    }

    private var hasNewDiscoverySuggestions: Bool {
        if !discoveryViewModel.runs.isEmpty {
            return discoveryViewModel.runs.contains { run in
                parseDate(run.runCreatedAt) > discoveryNewThreshold
            }
        }
        guard let runCreatedAt = discoveryViewModel.runCreatedAt else { return false }
        return parseDate(runCreatedAt) > discoveryNewThreshold
    }

    private var emptyStateView: some View {
        VStack(spacing: 20) {
            Image(systemName: "brain.head.profile")
                .font(.system(size: 48, weight: .light))
                .foregroundStyle(Color.accentColor.opacity(0.7))

            VStack(spacing: 6) {
                Text("No chats yet")
                    .font(.listTitle.weight(.semibold))
                    .foregroundStyle(Color.textPrimary)

                Text("Open any article and tap the")
                    .font(.listSubtitle)
                    .foregroundStyle(Color.textSecondary)
                +
                Text(" \(Image(systemName: "brain.head.profile")) ")
                    .font(.listSubtitle)
                    .foregroundStyle(Color.accentColor)
                +
                Text("icon to start a conversation about it.")
                    .font(.listSubtitle)
                    .foregroundStyle(Color.textSecondary)
            }
            .multilineTextAlignment(.center)
            .frame(maxWidth: 280)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.surfacePrimary)
    }

    /// Parse a date string to Date
    private func parseDate(_ dateString: String) -> Date {
        // Try ISO8601 with fractional seconds
        let iso8601WithFractional = ISO8601DateFormatter()
        iso8601WithFractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = iso8601WithFractional.date(from: dateString) {
            return date
        }

        // Try ISO8601 without fractional seconds
        let iso8601 = ISO8601DateFormatter()
        iso8601.formatOptions = [.withInternetDateTime]
        if let date = iso8601.date(from: dateString) {
            return date
        }

        return Date.distantPast
    }

    private var sessionListView: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                chatSearchBarRow
                    .padding(.horizontal, 16)

                ForEach(filteredSessions) { session in
                    Button {
                        if session.sessionType == "voice_live" {
                            onSelectSession?(
                                ChatSessionRoute(
                                    sessionId: session.id,
                                    mode: .live,
                                    contentId: session.contentId
                                )
                            )
                        } else {
                            onSelectSession?(ChatSessionRoute(sessionId: session.id))
                        }
                    } label: {
                        ChatSessionCard(session: session)
                    }
                    .buttonStyle(.plain)
                    .padding(.horizontal, 16)
                    .contextMenu {
                        Button(role: .destructive) {
                            Task { await viewModel.deleteSessions(ids: [session.id]) }
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                    }
                }

                if shouldShowNoResults {
                    noResultsRow
                }
            }
            .padding(.vertical, 8)
        }
        .refreshable {
            await viewModel.loadSessions()
        }
    }

    private var filteredSessions: [ChatSessionSummary] {
        let trimmedQuery = chatSearchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedQuery.isEmpty else { return chatSessions }
        return chatSessions.filter { session in
            sessionMatchesSearch(session, query: trimmedQuery)
        }
    }

    private var shouldShowNoResults: Bool {
        let trimmedQuery = chatSearchText.trimmingCharacters(in: .whitespacesAndNewlines)
        return !trimmedQuery.isEmpty && filteredSessions.isEmpty
    }

    private func sessionMatchesSearch(_ session: ChatSessionSummary, query: String) -> Bool {
        let haystacks = [
            session.displayTitle,
            session.displaySubtitle ?? "",
            session.articleTitle ?? "",
            session.articleSource ?? "",
            session.topic ?? ""
        ]
        return haystacks.contains { $0.localizedCaseInsensitiveContains(query) }
    }

    private var chatSearchBarRow: some View {
        SearchBar(
            placeholder: "Search history...",
            text: $chatSearchText
        )
    }

    private var noResultsRow: some View {
        VStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: Spacing.iconSize))
                .foregroundStyle(Color.textSecondary)
            Text("No matching chats")
                .font(.listSubtitle)
                .fontWeight(.semibold)
            Text("Try a different keyword.")
                .font(.listCaption)
                .foregroundStyle(Color.textTertiary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Spacing.sectionTop)
    }

    private func loadForSelectedTab() async {
        switch selectedTab {
        case .chats:
            await viewModel.loadSessions()
        case .discover:
            await discoveryViewModel.loadSuggestions()
        case .live, .favorites:
            return
        }
    }

    private func markDiscoverySeen() {
        if discoveryLastOpenedTimestamp > 0 {
            discoveryNewThreshold = Date(timeIntervalSince1970: discoveryLastOpenedTimestamp)
        }
        discoveryLastOpenedTimestamp = Date().timeIntervalSince1970
    }

    private func tabTitle(for tab: KnowledgeTab) -> String {
        if tab == .discover && hasNewDiscoverySuggestions {
            return "Discover •"
        }
        return tab.title
    }
}

private enum KnowledgeTab: String, CaseIterable {
    case discover
    case chats
    case live
    case favorites

    var title: String {
        switch self {
        case .discover:
            return "Discover"
        case .chats:
            return "Chats"
        case .live:
            return "Live"
        case .favorites:
            return "Favorites"
        }
    }
}

// MARK: - Session Card

struct ChatSessionCard: View {
    let session: ChatSessionSummary

    /// Whether this session was recently active (within last 5 minutes)
    private var isRecentlyActive: Bool {
        guard let dateStr = session.lastMessageAt else { return false }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var date = formatter.date(from: dateStr)
        if date == nil {
            formatter.formatOptions = [.withInternetDateTime]
            date = formatter.date(from: dateStr)
        }
        guard let date else { return false }
        return Date().timeIntervalSince(date) < 300
    }

    private enum BadgeStyle {
        case thinking
        case ready
        case none
    }

    private var badgeStyle: BadgeStyle {
        if session.isProcessing { return .thinking }
        if !session.isProcessing && session.hasAnyMessages && isRecentlyActive { return .ready }
        return .none
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Header row: title + badge + arrow
            HStack(spacing: 8) {
                Text(session.displayTitle)
                    .font(.listTitle.weight(.semibold))
                    .foregroundColor(.textPrimary)
                    .lineLimit(1)

                Spacer()

                statusBadge

                Image(systemName: "arrow.right")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(.textTertiary)
            }

            // Preview row
            previewRow
        }
        .padding(14)
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.borderSubtle, lineWidth: 1)
        )
    }

    @ViewBuilder
    private var statusBadge: some View {
        switch badgeStyle {
        case .thinking:
            HStack(spacing: 4) {
                ProgressView()
                    .scaleEffect(0.5)
                Text("THINKING")
                    .font(.listMono.weight(.semibold))
                    .tracking(0.5)
            }
            .foregroundColor(.textTertiary)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Color.secondary.opacity(0.1))
            .cornerRadius(4)

        case .ready:
            Text("READY")
                .font(.listMono.weight(.semibold))
                .tracking(0.5)
                .foregroundColor(.blue)
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(Color.blue.opacity(0.1))
                .cornerRadius(4)

        case .none:
            EmptyView()
        }
    }

    @ViewBuilder
    private var previewRow: some View {
        if let preview = session.lastMessagePreview, !preview.isEmpty {
            let role = session.lastMessageRole ?? "assistant"
            let prefix = role == "user" ? "You: " : "AI: "
            let prefixColor: Color = role == "user" ? .textPrimary : .blue

            (Text(prefix).foregroundColor(prefixColor).fontWeight(.medium) +
             Text(preview).foregroundColor(.textSecondary))
                .font(.listSubtitle)
                .lineLimit(2)
        } else if session.isEmptyFavorite, let summary = session.articleSummary, !summary.isEmpty {
            Text(summary)
                .font(.listSubtitle)
                .foregroundColor(.textSecondary)
                .lineLimit(2)
        } else if let subtitle = session.displaySubtitle {
            Text(subtitle)
                .font(.listSubtitle)
                .foregroundColor(.textSecondary)
                .lineLimit(2)
        }
    }
}

// MARK: - New Chat Sheet

struct NewChatSheet: View {
    let provider: ChatModelProvider
    @Binding var isPresented: Bool
    let onCreateSession: (ChatSessionSummary) -> Void

    @State private var initialMessage: String = ""
    @State private var isCreating = false
    @State private var errorMessage: String?
    @FocusState private var isTextFieldFocused: Bool

    private let chatService = ChatService.shared

    private var providerColor: Color {
        switch provider.accentColor {
        case "green": return .green
        case "orange": return .orange
        case "purple": return .purple
        default: return .blue
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            // Drag indicator
            RoundedRectangle(cornerRadius: 2.5)
                .fill(Color(.tertiaryLabel))
                .frame(width: 36, height: 5)
                .padding(.top, 8)

            // Provider header
            VStack(spacing: 8) {
                // Provider icon
                ZStack {
                    Circle()
                        .fill(providerColor.opacity(0.15))
                        .frame(width: 56, height: 56)

                    Image(provider.iconAsset)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .frame(width: 28, height: 28)
                }

                VStack(spacing: 2) {
                    Text(provider.displayName)
                        .font(.listTitle.weight(.semibold))

                    Text(provider.tagline)
                        .font(.listCaption)
                        .foregroundColor(.secondary)
                }
            }
            .padding(.top, 16)
            .padding(.bottom, 20)

            // Message input
            VStack(alignment: .leading, spacing: 8) {
                ZStack(alignment: .topLeading) {
                    if initialMessage.isEmpty {
                        Text("What would you like to explore?")
                            .font(.listSubtitle)
                            .foregroundColor(Color(.placeholderText))
                            .padding(.horizontal, 16)
                            .padding(.vertical, 14)
                    }

                    TextEditor(text: $initialMessage)
                        .font(.listSubtitle)
                        .scrollContentBackground(.hidden)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 10)
                        .focused($isTextFieldFocused)
                }
                .frame(height: 100)
                .background(Color(.secondarySystemBackground))
                .cornerRadius(12)

                if let error = errorMessage {
                    HStack(spacing: 4) {
                        Image(systemName: "exclamationmark.circle.fill")
                            .font(.listCaption)
                        Text(error)
                            .font(.listCaption)
                    }
                    .foregroundColor(.red)
                }
            }
            .padding(.horizontal, Spacing.screenHorizontal)

            HStack(spacing: 6) {
                Image(systemName: "star")
                    .font(.chipLabel)
                    .foregroundColor(.orange)
                Text("Favorite articles to chat about them with full context.")
                    .font(.listCaption)
                    .foregroundColor(.secondary)
            }
            .padding(.top, 10)
            .padding(.horizontal, Spacing.screenHorizontal)

            Spacer()

            // Action buttons
            VStack(spacing: 10) {
                Button {
                    Task { await createSession() }
                } label: {
                    HStack {
                        if isCreating {
                            ProgressView()
                                .progressViewStyle(CircularProgressViewStyle(tint: .white))
                                .scaleEffect(0.8)
                        } else {
                            Image(systemName: "paperplane.fill")
                        }
                        Text(initialMessage.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                             ? "Start Chat"
                             : "Send")
                    }
                    .font(.listSubtitle.weight(.semibold))
                    .foregroundColor(.white)
                    .frame(maxWidth: .infinity)
                    .frame(height: 50)
                    .background(providerColor)
                    .cornerRadius(12)
                }
                .disabled(isCreating)

                Button {
                    isPresented = false
                } label: {
                    Text("Cancel")
                        .font(.listSubtitle)
                        .foregroundColor(.secondary)
                }
                .padding(.bottom, 8)
            }
            .padding(.horizontal, Spacing.screenHorizontal)
            .padding(.bottom, 16)
        }
        .onAppear {
            isTextFieldFocused = true
        }
    }

    private func createSession() async {
        isCreating = true
        errorMessage = nil

        do {
            let session = try await chatService.startAdHocChat(
                initialMessage: initialMessage.isEmpty ? nil : initialMessage,
                provider: provider
            )
            onCreateSession(session)
            isPresented = false
        } catch {
            errorMessage = error.localizedDescription
        }

        isCreating = false
    }
}

#Preview {
    KnowledgeView()
}
