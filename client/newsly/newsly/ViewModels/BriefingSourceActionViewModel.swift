import Foundation
import Observation

/// Commands launched from a Briefing source. The caller owns navigation and task lifetime.
@MainActor
@Observable
final class BriefingSourceActionViewModel {
    private enum Action {
        case save, chat, deck, deckLookup
    }

    private var activeAction: Action?
    private(set) var errorMessage: String?

    var isSaving: Bool { activeAction == .save }
    var isStartingChat: Bool { activeAction == .chat }
    var isCreatingDeck: Bool { activeAction == .deck }
    var isBusy: Bool { activeAction != nil }

    @ObservationIgnored
    private let saveSource: (KnowledgeSource) async throws -> String
    @ObservationIgnored
    private let startSourceChat: (KnowledgeSource) async throws -> ChatSessionRoute
    @ObservationIgnored
    private let createSourceDeck: (KnowledgeSource, String?) async throws -> LearningDeck
    @ObservationIgnored
    private let findSourceDeck: (KnowledgeSource, String?) async throws -> LearningDeck?

    init(
        saveSource: @escaping (KnowledgeSource) async throws -> String,
        startSourceChat: @escaping (KnowledgeSource) async throws -> ChatSessionRoute,
        findSourceDeck: @escaping (KnowledgeSource, String?) async throws -> LearningDeck? = { _, _ in nil },
        createSourceDeck: @escaping (KnowledgeSource, String?) async throws -> LearningDeck
    ) {
        self.saveSource = saveSource
        self.startSourceChat = startSourceChat
        self.createSourceDeck = createSourceDeck
        self.findSourceDeck = findSourceDeck
    }

    /// Briefing's namespace is authoritative, including legacy content with a news type.
    static func knowledgeSource(for source: APIBriefingSource) -> KnowledgeSource? {
        switch source.kind {
        case "news": .news(source.id)
        case "content": .content(source.id)
        default: nil
        }
    }

    func clearError() {
        errorMessage = nil
    }

    func save(source: KnowledgeSource) async -> String? {
        await perform(.save) { try await self.saveSource(source) }
    }

    func startChat(source: KnowledgeSource) async -> ChatSessionRoute? {
        await perform(.chat) { try await self.startSourceChat(source) }
    }

    func createDeck(source: KnowledgeSource, focus: String?) async -> LearningDeck? {
        let trimmedFocus = focus?.trimmingCharacters(in: .whitespacesAndNewlines)
        return await perform(.deck) {
            try await self.createSourceDeck(
                source,
                trimmedFocus?.isEmpty == false ? trimmedFocus : nil
            )
        }
    }

    func existingDeck(source: KnowledgeSource, sourceURL: String? = nil) async -> LearningDeck? {
        await perform(.deckLookup) { try await self.findSourceDeck(source, sourceURL) } ?? nil
    }

    private func perform<Value>(
        _ action: Action,
        operation: () async throws -> Value
    ) async -> Value? {
        guard !isBusy, !Task.isCancelled else { return nil }
        activeAction = action
        errorMessage = nil
        defer { activeAction = nil }

        do {
            let value = try await operation()
            guard !Task.isCancelled else { return nil }
            return value
        } catch {
            guard !Task.isCancelled, ClientFailure.classify(error) != .cancelled else {
                return nil
            }
            errorMessage = error.localizedDescription
            return nil
        }
    }
}
