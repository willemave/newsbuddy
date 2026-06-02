//
//  CustomNarrationCreationViewModel.swift
//  newsly
//

import Foundation

struct CustomNarrationPollKey: Equatable {
    let episodeId: Int?
    let shouldPoll: Bool
}

struct CustomNarrationSourceSelection {
    let contentIds: [Int]
    let newsItemIds: [Int]
    let markSourceContentReadOnPlay: Bool

    var isEmpty: Bool {
        contentIds.isEmpty && newsItemIds.isEmpty
    }
}

@MainActor
final class CustomNarrationCreationViewModel: ObservableObject {
    @Published private(set) var isCreating = false
    @Published private(set) var episode: AudioEpisode?
    @Published var errorMessage: String?

    var isGenerating: Bool {
        isCreating || episode?.isGenerating == true
    }

    func pollKey(isActive: Bool) -> CustomNarrationPollKey {
        CustomNarrationPollKey(
            episodeId: episode?.id,
            shouldPoll: isActive
        )
    }

    func create(from selection: CustomNarrationSourceSelection) async -> Bool {
        guard !selection.isEmpty, !isCreating else { return false }
        isCreating = true
        errorMessage = nil
        defer { isCreating = false }

        do {
            let createdEpisode = try await AudioEpisodeService.shared.createCustomNarrationEpisode(
                contentIds: selection.contentIds,
                newsItemIds: selection.newsItemIds,
                markSourceContentReadOnPlay: selection.markSourceContentReadOnPlay,
                delivery: .background
            )
            episode = createdEpisode
            ToastService.shared.showSuccess(
                createdEpisode.isCompleted ? "Narration ready in Knowledge" : "Narration is generating"
            )
            return true
        } catch where isNetworkCancellation(error) {
            return false
        } catch {
            errorMessage = error.localizedDescription
            ToastService.shared.showError("Failed to create narration: \(error.localizedDescription)")
            return false
        }
    }

    func pollIfNeeded(isActive: Bool) async {
        guard isActive,
              let currentEpisode = episode,
              currentEpisode.isGenerating
        else { return }

        do {
            let completed = try await AudioEpisodeService.shared.waitForCompletedEpisode(
                currentEpisode,
                pollIntervalNanoseconds: 2_000_000_000,
                maxAttempts: 90
            )
            episode = completed
            ToastService.shared.showSuccess("Narration ready in Knowledge")
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            await handlePollError(error, episodeId: currentEpisode.id)
        }
    }

    private func handlePollError(_ error: Error, episodeId: Int) async {
        let nsError = error as NSError
        if nsError.domain == "AudioEpisodeService", nsError.code == 1 {
            let latest = try? await AudioEpisodeService.shared.fetchEpisode(id: episodeId)
            episode = latest
            errorMessage = latest?.errorMessage ?? error.localizedDescription
            ToastService.shared.showError(errorMessage ?? "Narration generation failed.")
            return
        }

        if nsError.domain == "AudioEpisodeService", nsError.code == 2 {
            let timeoutMessage = "Narration is still generating. Check Knowledge for status."
            errorMessage = timeoutMessage
            episode = nil
            ToastService.shared.show(timeoutMessage)
            return
        }

        errorMessage = error.localizedDescription
        episode = nil
    }
}
