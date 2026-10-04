import Foundation
@testable import newsly

final class TestCredentialSessionProvider: CredentialSessionProviding {
    var hasStoredCredentialMaterial: Bool

    init(hasStoredCredentialMaterial: Bool = false) {
        self.hasStoredCredentialMaterial = hasStoredCredentialMaterial
    }

    func accessToken(for authentication: RequestAuthentication) async throws -> String? {
        guard authentication != .none else { return nil }
        throw ClientFailure.authenticationRequired
    }

    func refreshAfterRejection(rejectedAccessToken _: String?) async throws -> String {
        throw ClientFailure.authenticationExpired
    }
}

func makeTestAPIClient(
    credentialSession: any CredentialSessionProviding = TestCredentialSessionProvider()
) -> APIClient {
    APIClient(
        session: URLSession(configuration: .ephemeral),
        baseURLProvider: { URL(string: "https://example.invalid") },
        credentialSession: credentialSession
    )
}

func makeTestOpenAIService(
    credentialSession: any CredentialSessionProviding = TestCredentialSessionProvider()
) -> OpenAIService {
    OpenAIService(
        apiClient: makeTestAPIClient(credentialSession: credentialSession),
        credentialSession: credentialSession
    )
}

func makeTestCredentialSession(
    tokenStore: any AuthTokenStore = AuthenticationTestTokenStore(
        accessToken: nil,
        refreshToken: nil,
        userID: nil
    )
) -> CredentialSession {
    let lockURL = FileManager.default.temporaryDirectory.appendingPathComponent(
        "newsly-test-credentials-\(UUID().uuidString).lock"
    )
    return CredentialSession(
        storage: KeychainCredentialStorage(keychain: tokenStore),
        exchange: TestUnavailableRefreshExchange(),
        processLock: AuthRefreshProcessLock(fileURLProvider: { lockURL }),
        attemptStore: TestRefreshAttemptStore(),
        cooldownSeconds: 0
    )
}

@MainActor
func makeTestBadgeStatsStore() -> BadgeStatsStore {
    BadgeStatsStore(
        fetchStats: {
            throw CancellationError()
        }
    )
}

@MainActor
func makeTestReadStateCache() -> ReadStateCache {
    ReadStateCache(
        contentReadRepository: TestNoopReadStatusRepository(),
        newsReadRepository: TestNoopReadStatusRepository(),
        badgeStatsStore: makeTestBadgeStatsStore()
    )
}

final class TestNoopReadStatusRepository: ReadStatusRepositoryType {
    func markRead(ids _: [Int]) async throws {}
}

@MainActor
final class TestUnavailableSpeechTranscriber: SpeechTranscribing {
    var isAvailable: Bool { false }

    func makeSession(deadlines _: SpeechRecordingDeadlines) throws -> SpeechTranscriptionSession {
        throw VoiceDictationError.notAuthenticated
    }
}

private final class TestUnavailableRefreshExchange: RefreshTokenExchanging {
    func exchange(refreshToken _: String, attemptID _: String) async throws -> CredentialTokens {
        throw ClientFailure.authenticationExpired
    }
}

private final class TestRefreshAttemptStore: RefreshAttemptStoring {
    func attemptID(for _: String) throws -> String { UUID().uuidString }
    func clearAttempt(for _: String) {}
}

func decodeAPIContentDetailFixture(from data: Data) throws -> APIContentDetailResponse {
    guard var payload = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        throw CocoaError(.propertyListReadCorrupt)
    }
    let null = NSNull()
    let nullableDefaults: [String: Any] = [
        "source_url": null,
        "discussion_url": null,
        "title": null,
        "source": null,
        "error_message": null,
        "updated_at": null,
        "processed_at": null,
        "checked_out_by": null,
        "checked_out_at": null,
        "publication_date": null,
        "summary": null,
        "short_summary": null,
        "summary_kind": null,
        "summary_version": null,
        "structured_summary": null,
        "longform_artifact": null,
        "feed_preview": null,
        "artifact_type": null,
        "preview_bullets": null,
        "reason_to_read": null,
        "full_markdown": null,
        "body_kind": null,
        "body_format": null,
        "news_article_url": null,
        "news_discussion_url": null,
        "news_key_points": null,
        "news_summary": null,
        "image_url": null,
        "thumbnail_url": null,
        "detected_feed": null,
    ]
    for (key, value) in nullableDefaults where payload[key] == nil {
        payload[key] = value
    }
    payload["display_title"] = payload["display_title"]
        ?? payload["title"]
        ?? payload["url"]
        ?? "Untitled"
    payload["retry_count"] = payload["retry_count"] ?? 0
    payload["metadata"] = payload["metadata"] ?? [:]
    payload["is_read"] = payload["is_read"] ?? false
    payload["is_saved_to_knowledge"] = payload["is_saved_to_knowledge"] ?? false
    payload["body_available"] = payload["body_available"] ?? false
    payload["can_subscribe"] = payload["can_subscribe"] ?? false

    let normalized = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    return try JSONDecoder().decode(APIContentDetailResponse.self, from: normalized)
}

final class TestUnavailableMessageStatusService: MessageStatusFetching {
    func getMessageStatus(messageId: Int) async throws -> MessageStatusResponse {
        throw CancellationError()
    }
}

@MainActor
final class TestNoopChatCompletionNotifier: ChatCompletionNotifying {
    func showChatCompletedNotification(sessionId: Int, title: String, message: String) {}
}

@MainActor
func makeTestActiveChatSessionManager(
    messageCompletionRegistry: ChatMessageCompletionRegistry? = nil,
    startsPolling: Bool = false
) -> ActiveChatSessionManager {
    ActiveChatSessionManager(
        messageCompletionRegistry: messageCompletionRegistry
            ?? ChatMessageCompletionRegistry(statusService: TestUnavailableMessageStatusService()),
        notificationService: TestNoopChatCompletionNotifier(),
        startsPolling: startsPolling
    )
}

@MainActor
func waitUntil(
    attempts: Int = 100,
    condition: @escaping @MainActor () -> Bool
) async -> Bool {
    for _ in 0..<attempts {
        if condition() { return true }
        await Task.yield()
    }
    return condition()
}

extension ChatSessionSummary {
    static func fixture(
        id: Int = 1,
        contentId: Int? = nil,
        newsItemId: Int? = nil,
        title: String? = "Chat",
        sessionType: String? = "knowledge_chat",
        topic: String? = nil,
        llmProvider: String = "openai",
        llmModel: String = "openai:gpt-5.5",
        createdAt: Date = Date(timeIntervalSince1970: 0),
        updatedAt: Date? = nil,
        lastMessageAt: Date? = nil,
        articleTitle: String? = nil,
        articleUrl: String? = nil,
        articleSummary: String? = nil,
        articleSource: String? = nil,
        hasPendingMessage: Bool? = nil,
        isWaitingForContent: Bool? = nil,
        isSavedToKnowledge: Bool? = nil,
        hasMessages: Bool? = nil,
        lastMessagePreview: String? = nil,
        lastMessageRole: String? = nil
    ) -> ChatSessionSummary {
        ChatSessionSummary(
            id: id,
            contentId: contentId,
            newsItemId: newsItemId,
            title: title,
            sessionType: sessionType,
            topic: topic,
            llmProvider: llmProvider,
            llmModel: llmModel,
            createdAt: createdAt,
            updatedAt: updatedAt,
            lastMessageAt: lastMessageAt,
            articleTitle: articleTitle,
            articleUrl: articleUrl,
            articleSummary: articleSummary,
            articleSource: articleSource,
            hasPendingMessage: hasPendingMessage ?? false,
            isWaitingForContent: isWaitingForContent ?? false,
            isSavedToKnowledge: isSavedToKnowledge ?? false,
            hasMessages: hasMessages ?? true,
            lastMessagePreview: lastMessagePreview,
            lastMessageRole: lastMessageRole
        )
    }
}
