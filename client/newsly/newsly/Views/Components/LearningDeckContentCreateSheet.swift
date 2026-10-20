//
//  LearningDeckContentCreateSheet.swift
//  newsly
//

import SwiftUI

struct LearningDeckContentCreateSheet: View {
    let content: ContentDetail
    let focusRecorder: LearningDeckFocusRecorder
    let learningDeckService: LearningDeckService
    let onCreated: (LearningDeck) -> Void
    let onNotice: (String, String) -> Void

    @State private var isSubmitting = false

    var body: some View {
        LearningDeckCreateSheet(
            sourceTitle: content.displayTitle,
            isSubmitting: isSubmitting,
            focusRecorder: focusRecorder,
            onCreate: createDeck
        )
    }

    @MainActor
    private func createDeck(interestsPrompt: String?) async -> Bool {
        guard !isSubmitting else { return false }

        isSubmitting = true
        defer { isSubmitting = false }

        do {
            // Creation returns the user to where they started; the deck generates in
            // the background and appears in Knowledge.
            onCreated(try await createDeck(for: content, interestsPrompt: interestsPrompt))
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
