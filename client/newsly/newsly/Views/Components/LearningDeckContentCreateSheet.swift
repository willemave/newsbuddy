//
//  LearningDeckContentCreateSheet.swift
//  newsly
//

import SwiftUI

struct LearningDeckContentCreateSheet: View {
    let content: ContentDetail
    let onOpenURL: (URL) -> Void
    let onNotice: (String, String) -> Void

    @State private var isSubmitting = false

    var body: some View {
        LearningDeckCreateSheet(
            sourceTitle: content.displayTitle,
            requiresURL: false,
            isSubmitting: isSubmitting,
            onCreate: createDeck
        )
    }

    @MainActor
    private func createDeck(url _: String?, interestsPrompt: String?) async -> Bool {
        guard !isSubmitting else { return false }

        isSubmitting = true
        defer { isSubmitting = false }

        do {
            let deck = try await createDeck(for: content, interestsPrompt: interestsPrompt)
            if deck.viewerAvailable {
                let url = try await LearningDeckService.shared.viewerURL(deckId: deck.id)
                onOpenURL(url)
            } else {
                onNotice(
                    "Learning Deck queued",
                    deck.latestNote ?? "The deck is being generated."
                )
            }
            return true
        } catch where isNetworkCancellation(error) {
            return false
        } catch {
            onNotice("Learning Deck", error.localizedDescription)
            return false
        }
    }

    private func createDeck(
        for content: ContentDetail,
        interestsPrompt: String?
    ) async throws -> LearningDeck {
        if content.contentTypeEnum == .news {
            return try await LearningDeckService.shared.createDeck(
                newsItemId: content.id,
                interestsPrompt: interestsPrompt
            )
        }
        return try await LearningDeckService.shared.createDeck(
            contentId: content.id,
            interestsPrompt: interestsPrompt
        )
    }
}
