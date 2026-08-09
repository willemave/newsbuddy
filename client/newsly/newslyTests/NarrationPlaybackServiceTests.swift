import AVFoundation
import XCTest
@testable import newsly

@MainActor
final class NarrationPlaybackServiceTests: XCTestCase {
    func testStaleEndNotificationCannotStopReplacementPlayback() async throws {
        let service = makeService()
        let firstTarget = NarrationTarget.audioEpisode(41)
        let replacementTarget = NarrationTarget.audioEpisode(42)
        var finishedTargets: [NarrationTarget] = []
        let firstItem = try service.playAudioStream(
            resource(id: 41),
            for: firstTarget,
            onFinished: { finishedTargets.append($0) }
        )

        NotificationCenter.default.post(
            name: .AVPlayerItemDidPlayToEndTime,
            object: firstItem
        )
        _ = try service.playAudioStream(resource(id: 42), for: replacementTarget)
        await Task.yield()

        XCTAssertTrue(service.isSpeaking)
        XCTAssertEqual(service.speakingTarget, replacementTarget)
        XCTAssertTrue(finishedTargets.isEmpty)
        service.stop()
    }

    func testStaleFailureNotificationCannotStopReplacementPlayback() async throws {
        let service = makeService()
        let firstItem = try service.playAudioStream(
            resource(id: 41),
            for: .audioEpisode(41)
        )

        NotificationCenter.default.post(
            name: .AVPlayerItemFailedToPlayToEndTime,
            object: firstItem
        )
        _ = try service.playAudioStream(resource(id: 42), for: .audioEpisode(42))
        await Task.yield()

        XCTAssertTrue(service.isSpeaking)
        XCTAssertEqual(service.speakingTarget, .audioEpisode(42))
        service.stop()
    }

    func testPlaybackWithoutExplicitRatePreservesCurrentPreference() async throws {
        let service = makeService()
        service.setPlaybackRate(1.5)

        try await service.playStreamingNarration(for: .audioEpisode(42)) {
            self.resource(id: 42)
        }

        XCTAssertEqual(service.playbackRate, 1.5)
        service.stop()
    }

    func testSameTargetResumeReplacesCompletionHandler() async throws {
        let service = makeService()
        let target = NarrationTarget.audioEpisode(42)
        var firstFinishedTargets: [NarrationTarget] = []
        var resumedFinishedTargets: [NarrationTarget] = []
        var fetchCount = 0
        let item = try service.playAudioStream(
            resource(id: 42),
            for: target,
            onFinished: { firstFinishedTargets.append($0) }
        )
        service.pause()

        try await service.playStreamingNarration(
            for: target,
            onFinished: { resumedFinishedTargets.append($0) }
        ) {
            fetchCount += 1
            return self.resource(id: 42)
        }
        NotificationCenter.default.post(
            name: .AVPlayerItemDidPlayToEndTime,
            object: item
        )
        await Task.yield()

        XCTAssertEqual(fetchCount, 0)
        XCTAssertTrue(firstFinishedTargets.isEmpty)
        XCTAssertEqual(resumedFinishedTargets, [target])
    }

    private func makeService() -> NarrationPlaybackService {
        let suiteName = "NarrationPlaybackServiceTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defaults.removePersistentDomain(forName: suiteName)
        let preferenceStore = NarrationPlaybackPreferenceStore(
            defaults: defaults,
            storageKey: "playbackRate"
        )
        return NarrationPlaybackService(preferenceStore: preferenceStore)
    }

    private func resource(id: Int) -> AuthorizedMediaResource {
        AuthorizedMediaResource(
            url: URL(string: "https://example.test/audio/\(id).mp3")!,
            headers: [:]
        )
    }
}
