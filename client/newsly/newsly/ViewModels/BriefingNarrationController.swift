import Foundation
import Observation
import os.log

private let briefingNarrationLogger = Logger(
    subsystem: "com.newsly",
    category: "BriefingNarration"
)

@MainActor
protocol BriefingAudioEpisodeServicing: AnyObject {
    func streamResource(for episode: AudioEpisode) async throws -> AuthorizedMediaResource
}

extension AudioEpisodeService: BriefingAudioEpisodeServicing {}

@MainActor
protocol BriefingNarrationPlaybackControlling: AnyObject {
    var isSpeaking: Bool { get }
    var playbackRate: Float { get }
    var speakingTarget: NarrationTarget? { get }

    func pause()
    func stop()
    func playStreamingNarration(
        for target: NarrationTarget,
        rate: Float,
        metadata: NarrationPlaybackMetadata?,
        remotePrevious: (@MainActor () -> Void)?,
        remoteNext: (@MainActor () -> Void)?,
        onFinished: NarrationPlaybackFinishedHandler?,
        fetchStreamResource: () async throws -> AuthorizedMediaResource
    ) async throws
}

extension NarrationPlaybackService: BriefingNarrationPlaybackControlling {}

struct BriefingNarrationSession {
    var manifest: BriefingNarration?
    var selectedChapterIndex = 0
    var isPreparing = false
    var errorMessage: String?
}

@MainActor
@Observable
final class BriefingNarrationController {
    private enum PreparationOutcome {
        case ready(BriefingNarration)
        case failed(Error, cachedNarration: BriefingNarration?)
    }

    private struct Preparation {
        let task: Task<Void, Never>
        var waiters: [UUID: CheckedContinuation<BriefingNarration, Error>]
    }

    private(set) var sessions: [String: BriefingNarrationSession] = [:]

    @ObservationIgnored
    private let briefingService: any BriefingServicing
    @ObservationIgnored
    private let audioEpisodeService: any BriefingAudioEpisodeServicing
    @ObservationIgnored
    private let playbackService: any BriefingNarrationPlaybackControlling
    @ObservationIgnored
    private let pollIntervalNanoseconds: UInt64
    @ObservationIgnored
    private let pollMaxAttempts: Int
    @ObservationIgnored
    private var preparations: [String: Preparation] = [:]
    @ObservationIgnored
    private var playbackIntentID = UUID()

    init(
        briefingService: any BriefingServicing,
        audioEpisodeService: any BriefingAudioEpisodeServicing,
        playbackService: any BriefingNarrationPlaybackControlling,
        pollIntervalNanoseconds: UInt64 = 1_500_000_000,
        pollMaxAttempts: Int = 120
    ) {
        self.briefingService = briefingService
        self.audioEpisodeService = audioEpisodeService
        self.playbackService = playbackService
        self.pollIntervalNanoseconds = pollIntervalNanoseconds
        self.pollMaxAttempts = max(1, pollMaxAttempts)
    }

    deinit {
        for preparation in preparations.values {
            preparation.task.cancel()
            for continuation in preparation.waiters.values {
                continuation.resume(throwing: CancellationError())
            }
        }
    }

    func session(for lensKey: String) -> BriefingNarrationSession {
        sessions[lensKey] ?? BriefingNarrationSession()
    }

    func narration(for lensKey: String) -> BriefingNarration? {
        sessions[lensKey]?.manifest
    }

    func narrationChapterIndex(for lensKey: String) -> Int {
        session(for: lensKey).selectedChapterIndex
    }

    func narrationEpisode(for lensKey: String) -> AudioEpisode? {
        let session = session(for: lensKey)
        guard let narration = session.manifest,
              narration.chapters.indices.contains(session.selectedChapterIndex) else {
            return nil
        }
        return narration.chapters[session.selectedChapterIndex]
    }

    func isPlaying(lensKey: String) -> Bool {
        guard let episode = narrationEpisode(for: lensKey) else { return false }
        return playbackService.speakingTarget == .audioEpisode(episode.id)
            && playbackService.isSpeaking
    }

    func nextNarrationChapterIndex(
        afterFinishedEpisodeID episodeID: Int,
        chapterIndex: Int,
        for lensKey: String
    ) -> Int? {
        let session = session(for: lensKey)
        guard let narration = session.manifest,
              narration.chapters.indices.contains(chapterIndex),
              narration.chapters[chapterIndex].id == episodeID,
              session.selectedChapterIndex == chapterIndex,
              narration.chapters.indices.contains(chapterIndex + 1) else {
            return nil
        }
        return chapterIndex + 1
    }

    func togglePlayback(for lensKey: String) async {
        clearError(for: lensKey)
        let target = narrationEpisode(for: lensKey).map { NarrationTarget.audioEpisode($0.id) }
        if let target,
           playbackService.speakingTarget == target,
           playbackService.isSpeaking {
            _ = beginPlaybackIntent()
            playbackService.pause()
            return
        }
        guard !session(for: lensKey).isPreparing else { return }
        let playbackIntentID = beginPlaybackIntent()
        await playChapter(
            at: narrationChapterIndex(for: lensKey),
            for: lensKey,
            playbackIntentID: playbackIntentID
        )
    }

    func playChapter(at chapterIndex: Int, for lensKey: String) async {
        guard !session(for: lensKey).isPreparing else { return }
        let playbackIntentID = beginPlaybackIntent()
        await playChapter(
            at: chapterIndex,
            for: lensKey,
            playbackIntentID: playbackIntentID
        )
    }

    private func playChapter(
        at chapterIndex: Int,
        for lensKey: String,
        playbackIntentID: UUID
    ) async {
        guard self.playbackIntentID == playbackIntentID else { return }
        guard !session(for: lensKey).isPreparing else { return }
        clearError(for: lensKey)

        if let narration = narration(for: lensKey),
           narration.chapters.indices.contains(chapterIndex) {
            let requestedTarget = NarrationTarget.audioEpisode(narration.chapters[chapterIndex].id)
            if let speakingTarget = playbackService.speakingTarget,
               speakingTarget != requestedTarget {
                playbackService.stop()
            }
        }

        updateSession(for: lensKey) { $0.isPreparing = true }
        defer { updateSession(for: lensKey) { $0.isPreparing = false } }

        do {
            let episode = try await prepareNarrationChapter(at: chapterIndex, for: lensKey)
            guard self.playbackIntentID == playbackIntentID else { return }
            guard let narration = narration(for: lensKey),
                  narration.chapters.indices.contains(chapterIndex) else { return }
            let metadata = NarrationPlaybackMetadata(
                title: episode.title,
                collectionTitle: narration.collectionTitle,
                subtitle: episode.subtitle,
                artworkURL: ServerImageURL.resolve(episode.artworkUrl),
                chapterIndex: chapterIndex,
                chapterCount: narration.chapters.count
            )
            let remotePrevious: (@MainActor () -> Void)?
            if chapterIndex > 0 {
                remotePrevious = { [weak self] in
                    Task<Void, Never> { @MainActor [weak self] in
                        await self?.playChapter(at: chapterIndex - 1, for: lensKey)
                    }
                }
            } else {
                remotePrevious = nil
            }
            let remoteNext: (@MainActor () -> Void)?
            if narration.chapters.indices.contains(chapterIndex + 1) {
                remoteNext = { [weak self] in
                    Task<Void, Never> { @MainActor [weak self] in
                        await self?.playChapter(at: chapterIndex + 1, for: lensKey)
                    }
                }
            } else {
                remoteNext = nil
            }
            try await playbackService.playStreamingNarration(
                for: .audioEpisode(episode.id),
                rate: playbackService.playbackRate,
                metadata: metadata,
                remotePrevious: remotePrevious,
                remoteNext: remoteNext,
                onFinished: { [weak self] finishedTarget in
                    Task { @MainActor [weak self] in
                        await self?.advanceNarration(
                            after: finishedTarget,
                            chapterIndex: chapterIndex,
                            lensKey: lensKey,
                            playbackIntentID: playbackIntentID
                        )
                    }
                }
            ) { [audioEpisodeService] in
                try await audioEpisodeService.streamResource(for: episode)
            }
        } catch where ClientFailure.classify(error) == .cancelled {
            return
        } catch {
            guard self.playbackIntentID == playbackIntentID else { return }
            briefingNarrationLogger.error(
                "Narration playback failed | lensKey=\(lensKey, privacy: .public) error=\(error.localizedDescription, privacy: .private)"
            )
            let message = (error as? AudioEpisodeServiceError)?.userFacingMessage
                ?? AudioEpisodeServiceError.generationFailed.userFacingMessage
            updateSession(for: lensKey) { $0.errorMessage = message }
        }
    }

    func prepareNarration(for lensKey: String) async throws -> BriefingNarration {
        let waiterID = UUID()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                if Task.isCancelled {
                    continuation.resume(throwing: CancellationError())
                    return
                }
                registerPreparationWaiter(continuation, id: waiterID, for: lensKey)
            }
        } onCancel: {
            Task { @MainActor [weak self] in
                self?.cancelPreparationWaiter(id: waiterID, for: lensKey)
            }
        }
    }

    func prepareNarrationChapter(
        at chapterIndex: Int,
        for lensKey: String
    ) async throws -> AudioEpisode {
        var currentNarration: BriefingNarration
        if let cachedNarration = narration(for: lensKey) {
            currentNarration = cachedNarration
        } else {
            currentNarration = try await prepareNarration(for: lensKey)
        }

        guard currentNarration.chapters.indices.contains(chapterIndex) else {
            throw AudioEpisodeServiceError.generationFailed
        }
        updateSession(for: lensKey) { $0.selectedChapterIndex = chapterIndex }

        if currentNarration.chapters[chapterIndex].isFailed {
            currentNarration = try await briefingService.requestNarration(programKey: lensKey)
            try Task.checkCancellation()
            storeNarration(currentNarration, for: lensKey)
        }

        for attempt in 0..<pollMaxAttempts {
            guard currentNarration.chapters.indices.contains(chapterIndex) else {
                throw AudioEpisodeServiceError.generationFailed
            }
            let chapter = currentNarration.chapters[chapterIndex]
            if chapter.isCompleted {
                return chapter
            }
            if chapter.isFailed {
                throw AudioEpisodeServiceError.generationFailed
            }

            currentNarration = try await briefingService.fetchNarration(
                episodeGroupID: currentNarration.episodeGroupId
            )
            try Task.checkCancellation()
            storeNarration(currentNarration, for: lensKey)
            guard currentNarration.chapters.indices.contains(chapterIndex) else {
                throw AudioEpisodeServiceError.generationFailed
            }
            if currentNarration.chapters[chapterIndex].isCompleted {
                return currentNarration.chapters[chapterIndex]
            }
            if currentNarration.chapters[chapterIndex].isFailed {
                throw AudioEpisodeServiceError.generationFailed
            }
            if attempt < pollMaxAttempts - 1 {
                try await Task.sleep(nanoseconds: pollIntervalNanoseconds)
            }
        }

        throw AudioEpisodeServiceError.preparationTimedOut
    }

    func refresh(for lensKey: String) async {
        guard let current = narration(for: lensKey) else { return }
        do {
            let refreshed = try await briefingService.fetchNarration(
                episodeGroupID: current.episodeGroupId
            )
            try Task.checkCancellation()
            guard refreshed.episodeGroupId == current.episodeGroupId else {
                briefingNarrationLogger.error(
                    "Narration refresh returned the wrong group | lensKey=\(lensKey, privacy: .public) expectedGroup=\(current.episodeGroupId, privacy: .private) actualGroup=\(refreshed.episodeGroupId, privacy: .private)"
                )
                return
            }
            storeNarration(refreshed, for: lensKey)
        } catch where ClientFailure.classify(error) == .cancelled {
            return
        } catch {
            briefingNarrationLogger.error(
                "Narration refresh failed | lensKey=\(lensKey, privacy: .public) group=\(current.episodeGroupId, privacy: .private) error=\(error.localizedDescription, privacy: .private)"
            )
        }
    }

    private func registerPreparationWaiter(
        _ continuation: CheckedContinuation<BriefingNarration, Error>,
        id waiterID: UUID,
        for lensKey: String
    ) {
        if var preparation = preparations[lensKey] {
            preparation.waiters[waiterID] = continuation
            preparations[lensKey] = preparation
            return
        }

        let cachedNarration = narration(for: lensKey)
        let briefingService = briefingService
        let pollIntervalNanoseconds = pollIntervalNanoseconds
        let pollMaxAttempts = pollMaxAttempts
        let task = Task { @MainActor [weak self] in
            let outcome = await Self.prepareNarration(
                for: lensKey,
                cachedNarration: cachedNarration,
                briefingService: briefingService,
                pollIntervalNanoseconds: pollIntervalNanoseconds,
                maxAttempts: pollMaxAttempts
            )
            self?.finishPreparation(outcome, for: lensKey)
        }
        preparations[lensKey] = Preparation(
            task: task,
            waiters: [waiterID: continuation]
        )
    }

    private static func prepareNarration(
        for lensKey: String,
        cachedNarration: BriefingNarration?,
        briefingService: any BriefingServicing,
        pollIntervalNanoseconds: UInt64,
        maxAttempts: Int
    ) async -> PreparationOutcome {
        var current = cachedNarration
        do {
            if let current, current.playable, current.firstPlayableChapter != nil {
                return .ready(current)
            }

            if current?.isGenerating != true {
                current = try await briefingService.requestNarration(programKey: lensKey)
                try Task.checkCancellation()
            }

            guard var narration = current else {
                return .failed(AudioEpisodeServiceError.generationFailed, cachedNarration: nil)
            }
            if narration.playable, narration.firstPlayableChapter != nil {
                return .ready(narration)
            }
            if narration.status == .failed {
                return .failed(AudioEpisodeServiceError.generationFailed, cachedNarration: nil)
            }

            for _ in 0..<maxAttempts {
                try await Task.sleep(nanoseconds: pollIntervalNanoseconds)
                narration = try await briefingService.fetchNarration(
                    episodeGroupID: narration.episodeGroupId
                )
                current = narration
                try Task.checkCancellation()
                if narration.playable, narration.firstPlayableChapter != nil {
                    return .ready(narration)
                }
                if narration.status == .failed {
                    return .failed(AudioEpisodeServiceError.generationFailed, cachedNarration: nil)
                }
            }

            return .failed(AudioEpisodeServiceError.preparationTimedOut, cachedNarration: current)
        } catch let error where ClientFailure.classify(error) == .cancelled {
            return .failed(error, cachedNarration: cachedNarration)
        } catch {
            return .failed(error, cachedNarration: current)
        }
    }

    private func finishPreparation(_ outcome: PreparationOutcome, for lensKey: String) {
        guard let preparation = preparations.removeValue(forKey: lensKey) else { return }
        do {
            let narration = try applyPreparation(outcome, for: lensKey)
            for continuation in preparation.waiters.values {
                continuation.resume(returning: narration)
            }
        } catch {
            for continuation in preparation.waiters.values {
                continuation.resume(throwing: error)
            }
        }
    }

    private func applyPreparation(
        _ outcome: PreparationOutcome,
        for lensKey: String
    ) throws -> BriefingNarration {
        switch outcome {
        case .ready(let narration):
            storeNarration(narration, for: lensKey)
            return narration
        case .failed(let error, let cachedNarration):
            if let cachedNarration {
                storeNarration(cachedNarration, for: lensKey)
            } else {
                updateSession(for: lensKey) {
                    $0.manifest = nil
                    $0.selectedChapterIndex = 0
                }
            }
            throw error
        }
    }

    private func cancelPreparationWaiter(id waiterID: UUID, for lensKey: String) {
        guard var preparation = preparations[lensKey],
              let continuation = preparation.waiters.removeValue(forKey: waiterID) else {
            return
        }
        continuation.resume(throwing: CancellationError())
        if preparation.waiters.isEmpty {
            preparations.removeValue(forKey: lensKey)
            preparation.task.cancel()
            return
        }
        preparations[lensKey] = preparation
    }

    private func advanceNarration(
        after finishedTarget: NarrationTarget,
        chapterIndex: Int,
        lensKey: String,
        playbackIntentID: UUID
    ) async {
        guard self.playbackIntentID == playbackIntentID,
              case .audioEpisode(let episodeID) = finishedTarget,
              let nextIndex = nextNarrationChapterIndex(
                afterFinishedEpisodeID: episodeID,
                chapterIndex: chapterIndex,
                for: lensKey
              ) else {
            return
        }
        await playChapter(
            at: nextIndex,
            for: lensKey,
            playbackIntentID: playbackIntentID
        )
    }

    private func beginPlaybackIntent() -> UUID {
        let id = UUID()
        playbackIntentID = id
        return id
    }

    private func storeNarration(_ narration: BriefingNarration, for lensKey: String) {
        updateSession(for: lensKey) { session in
            session.manifest = narration
            guard !narration.chapters.isEmpty else {
                session.selectedChapterIndex = 0
                return
            }
            session.selectedChapterIndex = min(
                max(session.selectedChapterIndex, 0),
                narration.chapters.count - 1
            )
        }
    }

    private func clearError(for lensKey: String) {
        updateSession(for: lensKey) { $0.errorMessage = nil }
    }

    private func updateSession(
        for lensKey: String,
        _ update: (inout BriefingNarrationSession) -> Void
    ) {
        var session = session(for: lensKey)
        update(&session)
        sessions[lensKey] = session
    }
}
