//
//  LearningDeckContentCreateSheet.swift
//  newsly
//

import SwiftUI

struct LearningDeckContentCreateSheet: View {
    let content: ContentDetail
    let focusRecorder: LearningDeckFocusRecorder
    let learningDeckService: LearningDeckService
    let onOpenDeck: (LearningDeck, URL?) -> Void
    let onNotice: (String, String) -> Void

    @State private var isSubmitting = false

    var body: some View {
        LearningDeckCreateSheet(
            sourceTitle: content.displayTitle,
            requiresURL: false,
            isSubmitting: isSubmitting,
            focusRecorder: focusRecorder,
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
                let url = try await learningDeckService.viewerURL(deckId: deck.id)
                onOpenDeck(deck, url)
            } else {
                // The deck is still generating — open the reader anyway so it can
                // show live progress and swap in the deck when it's ready, instead
                // of stranding the user on a dismiss-only alert.
                onOpenDeck(deck, nil)
            }
            return true
        } catch where ClientFailure.classify(error) == .cancelled {
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
        if content.contentType == .news {
            return try await learningDeckService.createDeck(
                newsItemId: content.id,
                interestsPrompt: interestsPrompt
            )
        }
        return try await learningDeckService.createDeck(
            contentId: content.id,
            interestsPrompt: interestsPrompt
        )
    }
}
