import XCTest
@testable import newsly

@MainActor
final class PodcastAudioControllerTests: XCTestCase {
    func testNavigationInvalidatesDeferredEpisodeCreation() async throws {
        let content = try makeContent(id: 41)
        let service = DeferredPodcastAudioEpisodeService(
            episodesByContentID: [content.id: makeEpisode(id: 141, contentID: content.id)],
            deferredCreationContentIDs: [content.id]
        )
        let playbackService = makePlaybackService()
        let controller = PodcastAudioController(
            playbackService: playbackService,
            audioEpisodeService: service
        )

        let playbackTask = Task { @MainActor in
            try await controller.handleAudio(for: content, currentContentId: content.id)
        }
        let didBeginCreation = await waitUntil {
            service.isCreationPending(for: content.id)
        }
        XCTAssertTrue(didBeginCreation)

        controller.stopIfSpeaking(forContentId: content.id)
        service.resolveCreation(for: content.id)
        try await playbackTask.value

        XCTAssertTrue(service.streamEpisodeIDs.isEmpty)
        XCTAssertFalse(playbackService.isSpeaking)
        XCTAssertNil(playbackService.speakingTarget)
    }

    func testDismissWithoutLoadedContentInvalidatesDeferredStream() async throws {
        let content = try makeContent(id: 42)
        let episode = makeEpisode(id: 142, contentID: content.id)
        let service = DeferredPodcastAudioEpisodeService(
            episodesByContentID: [content.id: episode],
            deferredStreamEpisodeIDs: [episode.id]
        )
        let playbackService = makePlaybackService()
        let controller = PodcastAudioController(
            playbackService: playbackService,
            audioEpisodeService: service
        )

        let playbackTask = Task { @MainActor in
            try await controller.handleAudio(for: content, currentContentId: content.id)
        }
        let didBeginStream = await waitUntil {
            service.isStreamPending(for: episode.id)
        }
        XCTAssertTrue(didBeginStream)

        controller.stopIfSpeaking(forContentId: nil)
        service.resolveStream(for: episode.id)
        try await playbackTask.value

        XCTAssertFalse(playbackService.isSpeaking)
        XCTAssertNil(playbackService.speakingTarget)
    }

    func testLatestContentWinsWhenEarlierEpisodeCreationFinishesLast() async throws {
        let firstContent = try makeContent(id: 43)
        let latestContent = try makeContent(id: 44)
        let firstEpisode = makeEpisode(id: 143, contentID: firstContent.id)
        let latestEpisode = makeEpisode(id: 144, contentID: latestContent.id)
        let service = DeferredPodcastAudioEpisodeService(
            episodesByContentID: [
                firstContent.id: firstEpisode,
                latestContent.id: latestEpisode,
            ],
            deferredCreationContentIDs: [firstContent.id, latestContent.id]
        )
        let playbackService = makePlaybackService()
        let controller = PodcastAudioController(
            playbackService: playbackService,
            audioEpisodeService: service
        )

        let firstTask = Task { @MainActor in
            try await controller.handleAudio(
                for: firstContent,
                currentContentId: firstContent.id
            )
        }
        let didBeginFirstCreation = await waitUntil {
            service.isCreationPending(for: firstContent.id)
        }
        XCTAssertTrue(didBeginFirstCreation)

        let latestTask = Task { @MainActor in
            try await controller.handleAudio(
                for: latestContent,
                currentContentId: latestContent.id
            )
        }
        let didBeginLatestCreation = await waitUntil {
            service.isCreationPending(for: latestContent.id)
        }
        XCTAssertTrue(didBeginLatestCreation)

        service.resolveCreation(for: latestContent.id)
        try await latestTask.value
        service.resolveCreation(for: firstContent.id)
        try await firstTask.value

        XCTAssertEqual(service.streamEpisodeIDs, [latestEpisode.id])
        XCTAssertEqual(playbackService.speakingTarget, .audioEpisode(latestEpisode.id))
        playbackService.stop()
    }

    private func makePlaybackService() -> NarrationPlaybackService {
        let suiteName = "PodcastAudioControllerTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defaults.removePersistentDomain(forName: suiteName)
        return NarrationPlaybackService(
            preferenceStore: NarrationPlaybackPreferenceStore(
                defaults: defaults,
                storageKey: "playbackRate"
            )
        )
    }

    private func makeContent(id: Int) throws -> ContentDetail {
        try ContentDetail(
            api: APIContentDetailResponse(
                id: id,
                contentType: .article,
                url: "https://example.test/content/\(id)",
                sourceUrl: nil,
                discussionUrl: nil,
                title: nil,
                displayTitle: "Content \(id)",
                source: nil,
                status: .completed,
                errorMessage: nil,
                retryCount: 0,
                metadata: [:],
                createdAt: try XCTUnwrap(ServerDate.parse("2026-08-07T12:00:00Z")),
                updatedAt: nil,
                processedAt: nil,
                checkedOutBy: nil,
                checkedOutAt: nil,
                publicationDate: nil,
                summary: nil,
                shortSummary: nil,
                summaryKind: nil,
                summaryVersion: nil,
                structuredSummary: nil,
                longformArtifact: nil,
                feedPreview: nil,
                artifactType: nil,
                previewBullets: nil,
                reasonToRead: nil,
                fullMarkdown: nil,
                bodyKind: nil,
                bodyFormat: nil,
                newsArticleUrl: nil,
                newsDiscussionUrl: nil,
                newsKeyPoints: nil,
                newsSummary: nil,
                imageUrl: nil,
                thumbnailUrl: nil,
                detectedFeed: nil
            )
        )
    }

    private func makeEpisode(id: Int, contentID: Int) -> AudioEpisode {
        AudioEpisode(
            id: id,
            kind: .content_council_discussion,
            status: .completed,
            title: "Content discussion",
            sourceContentId: contentID,
            sourceContentIds: [contentID],
            sourceCount: 1,
            sourceTitles: ["Content \(contentID)"],
            durationSeconds: nil,
            audioUrl: nil,
            streamUrl: nil,
            scriptText: nil,
            errorMessage: nil,
            createdAt: Date(timeIntervalSince1970: 1_800_000_200),
            updatedAt: nil
        )
    }

    private func waitUntil(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<200 {
            if condition() { return true }
            await Task.yield()
        }
        return condition()
    }
}

@MainActor
private final class DeferredPodcastAudioEpisodeService: PodcastAudioEpisodeServicing {
    private let episodesByContentID: [Int: AudioEpisode]
    private let deferredCreationContentIDs: Set<Int>
    private let deferredStreamEpisodeIDs: Set<Int>
    private var creationContinuations: [Int: CheckedContinuation<AudioEpisode, Error>] = [:]
    private var streamContinuations: [Int: CheckedContinuation<AuthorizedMediaResource, Error>] = [:]

    private(set) var streamEpisodeIDs: [Int] = []

    init(
        episodesByContentID: [Int: AudioEpisode],
        deferredCreationContentIDs: Set<Int> = [],
        deferredStreamEpisodeIDs: Set<Int> = []
    ) {
        self.episodesByContentID = episodesByContentID
        self.deferredCreationContentIDs = deferredCreationContentIDs
        self.deferredStreamEpisodeIDs = deferredStreamEpisodeIDs
    }

    func streamResource(for episode: AudioEpisode) async throws -> AuthorizedMediaResource {
        streamEpisodeIDs.append(episode.id)
        if deferredStreamEpisodeIDs.contains(episode.id) {
            return try await withCheckedThrowingContinuation { continuation in
                streamContinuations[episode.id] = continuation
            }
        }
        return resource(for: episode.id)
    }

    func createContentCouncilEpisode(
        contentId: Int,
        delivery: AudioEpisodeDelivery
    ) async throws -> AudioEpisode {
        _ = delivery
        return try await createEpisode(for: contentId)
    }

    func createNewsItemDiscussionEpisode(
        newsItemId: Int,
        delivery: AudioEpisodeDelivery
    ) async throws -> AudioEpisode {
        _ = delivery
        return try await createEpisode(for: newsItemId)
    }

    func isCreationPending(for contentID: Int) -> Bool {
        creationContinuations[contentID] != nil
    }

    func isStreamPending(for episodeID: Int) -> Bool {
        streamContinuations[episodeID] != nil
    }

    func resolveCreation(for contentID: Int) {
        guard let episode = episodesByContentID[contentID] else {
            XCTFail("Missing episode fixture for content \(contentID)")
            return
        }
        creationContinuations.removeValue(forKey: contentID)?.resume(returning: episode)
    }

    func resolveStream(for episodeID: Int) {
        streamContinuations.removeValue(forKey: episodeID)?.resume(
            returning: resource(for: episodeID)
        )
    }

    private func createEpisode(for contentID: Int) async throws -> AudioEpisode {
        guard let episode = episodesByContentID[contentID] else {
            throw NSError(domain: "DeferredPodcastAudioEpisodeService", code: contentID)
        }
        guard deferredCreationContentIDs.contains(contentID) else { return episode }
        return try await withCheckedThrowingContinuation { continuation in
            creationContinuations[contentID] = continuation
        }
    }

    private func resource(for episodeID: Int) -> AuthorizedMediaResource {
        AuthorizedMediaResource(
            url: URL(string: "https://example.test/audio/\(episodeID).mp3")!,
            headers: [:]
        )
    }
}
