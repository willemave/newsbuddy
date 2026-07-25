//
//  LearningDeckContentCreateSheet.swift
//  newsly
//

import SwiftUI

struct LearningDeckContentCreateSheet: View {
    let content: ContentDetail
    let onOpenDeck: (LearningDeck, URL?) -> Void
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
                onOpenDeck(deck, url)
            } else {
                // The deck is still generating — open the reader anyway so it can
                // show live progress and swap in the deck when it's ready, instead
                // of stranding the user on a dismiss-only alert.
                onOpenDeck(deck, nil)
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
        if content.contentType == .news {
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

/// One-shot coachmark popover shown the first time the Learning Deck entry point
/// appears in the article action bar.
struct LearningDeckEntryHint: View {
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "rectangle.on.rectangle")
                .font(.appSymbol(size: 18, weight: .semibold))
                .foregroundStyle(Color.brandPrimary)

            VStack(alignment: .leading, spacing: 3) {
                Text("Learning Deck")
                    .font(.terracottaBodyMedium.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                Text("Turn this into a deck you can flip through and chat with.")
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(14)
        .frame(width: 264)
    }
}
