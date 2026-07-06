//
//  CustomNarrationCreationViewModel.swift
//  newsly
//

import Foundation
import Observation

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

protocol CustomNarrationAudioServicing: AnyObject {
    func createCustomNarrationEpisode(
        contentIds: [Int],
        newsItemIds: [Int],
        title: String?,
        markSourceContentReadOnPlay: Bool,
        delivery: AudioEpisodeDelivery
    ) async throws -> AudioEpisode
    func waitForCompletedEpisode(
        _ episode: AudioEpisode,
        pollIntervalNanoseconds: UInt64,
        maxAttempts: Int
    ) async throws -> AudioEpisode
    func fetchEpisode(id: Int) async throws -> AudioEpisode
    func fetchCustomNarrationEpisodes(limit: Int) async throws -> [AudioEpisode]
    func streamResource(for episode: AudioEpisode) async throws -> AuthorizedMediaResource
    func enableEpisodeShare(id: Int) async throws -> AudioEpisodeShareResponse
}

extension AudioEpisodeService: CustomNarrationAudioServicing {}

@MainActor
@Observable
final class CustomNarrationCreationViewModel {
    private(set) var isCreating = false
    private(set) var episode: AudioEpisode?
    var errorMessage: String?

    @ObservationIgnored
    private let audioService: any CustomNarrationAudioServicing
    @ObservationIgnored
    private let toastPresenter: any ToastPresenting

    init(
        audioService: any CustomNarrationAudioServicing,
        toastPresenter: any ToastPresenting
    ) {
        self.audioService = audioService
        self.toastPresenter = toastPresenter
    }

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
            let createdEpisode = try await audioService.createCustomNarrationEpisode(
                contentIds: selection.contentIds,
                newsItemIds: selection.newsItemIds,
                title: nil,
                markSourceContentReadOnPlay: selection.markSourceContentReadOnPlay,
                delivery: .background
            )
            episode = createdEpisode
            toastPresenter.showSuccess(
                createdEpisode.isCompleted ? "Narration ready in Knowledge" : "Narration is generating"
            )
            return true
        } catch where isNetworkCancellation(error) {
            return false
        } catch {
            errorMessage = error.localizedDescription
            toastPresenter.showError("Failed to create narration: \(error.localizedDescription)")
            return false
        }
    }

    func pollIfNeeded(isActive: Bool) async {
        guard isActive,
              let currentEpisode = episode,
              currentEpisode.isGenerating
        else { return }

        do {
            let completed = try await audioService.waitForCompletedEpisode(
                currentEpisode,
                pollIntervalNanoseconds: 2_000_000_000,
                maxAttempts: 90
            )
            episode = completed
            toastPresenter.showSuccess("Narration ready in Knowledge")
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            await handlePollError(error, episodeId: currentEpisode.id)
        }
    }

    private func handlePollError(_ error: Error, episodeId: Int) async {
        let nsError = error as NSError
        if nsError.domain == "AudioEpisodeService", nsError.code == 1 {
            let latest = try? await audioService.fetchEpisode(id: episodeId)
            episode = latest
            errorMessage = latest?.errorMessage ?? error.localizedDescription
            toastPresenter.showError(errorMessage ?? "Narration generation failed.")
            return
        }

        if nsError.domain == "AudioEpisodeService", nsError.code == 2 {
            let timeoutMessage = "Narration is still generating. Check Knowledge for status."
            errorMessage = timeoutMessage
            episode = nil
            toastPresenter.show(timeoutMessage, type: .info, duration: 3.0)
            return
        }

        errorMessage = error.localizedDescription
        episode = nil
    }
}
