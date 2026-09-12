import Foundation
import Observation
import os.log

private let briefingNarrationLogger = Logger(
    subsystem: "com.newsly",
    category: "BriefingNarration"
)

/// Local monotonic milestones; group and episode IDs join these with server/player logs.
private struct BriefingNarrationTiming {
    let id = UUID()
    let startedAt = ContinuousClock.now

    func log(
        _ stage: String,
        narration: BriefingNarration? = nil,
        episodeID: Int? = nil,
        attempt: Int = 0,
        operationStartedAt: ContinuousClock.Instant? = nil
    ) {
        let elapsed = startedAt.duration(to: .now).components
        let operation = (operationStartedAt ?? startedAt).duration(to: .now).components
        let elapsedMs = elapsed.seconds * 1000 + elapsed.attoseconds / 1_000_000_000_000_000
        let operationMs = operation.seconds * 1000 + operation.attoseconds / 1_000_000_000_000_000
        briefingNarrationLogger.info(
            "Briefing audio timing | trace=\(id.uuidString, privacy: .public) stage=\(stage, privacy: .public) group=\(narration?.episodeGroupId ?? "none", privacy: .public) episodeId=\(episodeID ?? -1) attempt=\(attempt) elapsedMs=\(elapsedMs) operationMs=\(operationMs)"
        )
    }
}

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
    enum Edition {
        case current
        case finished(readMarks: Task<Void, Never>)
    }

    var manifest: BriefingNarration?
    var edition: Edition = .current
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
    /// The chapter preparation each lens is awaiting, so dismissing the
    /// player can cancel it instead of letting it finish and then play.
    private var chapterPreparations: [String: Task<AudioEpisode, Error>] = [:]
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
        let timing = BriefingNarrationTiming()
        timing.log("play_intent")
        if case .finished(let readMarks) = session(for: lensKey).edition {
            await readMarks.value
            timing.log("read_marks_finished")
            guard self.playbackIntentID == playbackIntentID else {
                timing.log("superseded")
                return
            }
            sessions[lensKey] = BriefingNarrationSession()
        }
        await playChapter(
            at: narrationChapterIndex(for: lensKey),
            for: lensKey,
            playbackIntentID: playbackIntentID,
            timing: timing
        )
    }

    /// Dismisses the player: stops audio, cancels a chapter still preparing,
    /// and clears any error, so the lens returns to its quiet resting chrome.
    func stopPlayback(for lensKey: String) {
        _ = beginPlaybackIntent()
        chapterPreparations[lensKey]?.cancel()
        playbackService.stop()
        clearError(for: lensKey)
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
        playbackIntentID: UUID,
        timing: BriefingNarrationTiming = BriefingNarrationTiming()
    ) async {
        guard self.playbackIntentID == playbackIntentID else { return }
        guard !session(for: lensKey).isPreparing else { return }
        clearError(for: lensKey)
        timing.log("chapter_preparation_started", narration: narration(for: lensKey))
        var startupOutcome = "superseded"
        defer { timing.log(startupOutcome, narration: narration(for: lensKey)) }

        let requestedTarget: NarrationTarget?
        if let narration = narration(for: lensKey),
           narration.chapters.indices.contains(chapterIndex) {
            requestedTarget = .audioEpisode(narration.chapters[chapterIndex].id)
        } else {
            requestedTarget = nil
        }
        if let speakingTarget = playbackService.speakingTarget,
           speakingTarget != requestedTarget {
            playbackService.stop()
        }

        updateSession(for: lensKey) {
            $0.edition = .current
            $0.isPreparing = true
        }
        defer { updateSession(for: lensKey) { $0.isPreparing = false } }

        let preparation = Task { [self] in
            try await self.prepareNarrationChapter(at: chapterIndex, for: lensKey)
        }
        chapterPreparations[lensKey] = preparation
        defer { chapterPreparations[lensKey] = nil }

        do {
            let episode = try await preparation.value
            guard self.playbackIntentID == playbackIntentID else { return }
            guard let narration = narration(for: lensKey),
                  narration.chapters.indices.contains(chapterIndex) else { return }
            timing.log("chapter_ready", narration: narration, episodeID: episode.id)
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
            timing.log("player_requested", narration: narration, episodeID: episode.id)
            try await playbackService.playStreamingNarration(
                for: .audioEpisode(episode.id),
                rate: playbackService.playbackRate,
                metadata: metadata,
                remotePrevious: remotePrevious,
                remoteNext: remoteNext,
                onFinished: { [weak self] finishedTarget, readMarks in
                    self?.finishNarrationChapter(
                        after: finishedTarget,
                        chapterIndex: chapterIndex,
                        lensKey: lensKey,
                        playbackIntentID: playbackIntentID,
                        readMarks: readMarks
                    )
                }
            ) { [audioEpisodeService] in
                let authorizationStartedAt = ContinuousClock.now
                timing.log("authorization_started", narration: narration, episodeID: episode.id)
                let resource = try await audioEpisodeService.streamResource(for: episode)
                timing.log(
                    "authorization_finished",
                    narration: narration,
                    episodeID: episode.id,
                    operationStartedAt: authorizationStartedAt
                )
                return resource
            }
            startupOutcome = "player_handoff_finished"
        } catch where ClientFailure.classify(error) == .cancelled {
            startupOutcome = "cancelled"
            return
        } catch {
            guard self.playbackIntentID == playbackIntentID else { return }
            startupOutcome = "failed"
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
        let timing = BriefingNarrationTiming()
        var outcome = "failed"
        var attempts = 0
        defer {
            timing.log(Task.isCancelled ? "cancelled" : outcome, narration: narration(for: lensKey), attempt: attempts)
        }
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
            currentNarration = try await Self.retryNarration(currentNarration, service: briefingService)
            storeNarration(currentNarration, for: lensKey)
        }

        for attempt in 0..<pollMaxAttempts {
            guard currentNarration.chapters.indices.contains(chapterIndex) else {
                throw AudioEpisodeServiceError.generationFailed
            }
            let chapter = currentNarration.chapters[chapterIndex]
            if chapter.isCompleted {
                outcome = "requested_chapter_ready"
                return chapter
            }
            if chapter.isFailed {
                throw AudioEpisodeServiceError.generationFailed
            }

            let pollStartedAt = ContinuousClock.now
            attempts = attempt + 1
            currentNarration = try await briefingService.fetchNarration(
                episodeGroupID: currentNarration.episodeGroupId
            )
            try Task.checkCancellation()
            storeNarration(currentNarration, for: lensKey)
            timing.log("chapter_poll_finished", narration: currentNarration, attempt: attempts, operationStartedAt: pollStartedAt)
            guard currentNarration.chapters.indices.contains(chapterIndex) else {
                throw AudioEpisodeServiceError.generationFailed
            }
            if currentNarration.chapters[chapterIndex].isCompleted {
                outcome = "requested_chapter_ready"
                return currentNarration.chapters[chapterIndex]
            }
            if currentNarration.chapters[chapterIndex].isFailed {
                throw AudioEpisodeServiceError.generationFailed
            }
            if attempt < pollMaxAttempts - 1 {
                try await Task.sleep(nanoseconds: pollIntervalNanoseconds)
            }
        }

        outcome = "timed_out"
        throw AudioEpisodeServiceError.preparationTimedOut
    }

    func refresh(for lensKey: String) async {
        guard let current = narration(for: lensKey) else { return }
        do {
            let refreshed = try await briefingService.fetchNarration(
                episodeGroupID: current.episodeGroupId
            )
            try Task.checkCancellation()
            guard narration(for: lensKey)?.episodeGroupId == current.episodeGroupId,
                  refreshed.episodeGroupId == current.episodeGroupId else {
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

    private static func retryNarration(
        _ narration: BriefingNarration,
        service: any BriefingServicing
    ) async throws -> BriefingNarration {
        let retried = try await service.retryNarration(episodeGroupID: narration.episodeGroupId)
        try Task.checkCancellation()
        guard retried.episodeGroupId == narration.episodeGroupId,
              retried.chapters.map(\.id) == narration.chapters.map(\.id) else {
            throw AudioEpisodeServiceError.generationFailed
        }
        return retried
    }

    private static func prepareNarration(
        for lensKey: String,
        cachedNarration: BriefingNarration?,
        briefingService: any BriefingServicing,
        pollIntervalNanoseconds: UInt64,
        maxAttempts: Int
    ) async -> PreparationOutcome {
        let timing = BriefingNarrationTiming()
        var outcome = "failed"
        var attempts = 0
        var current = cachedNarration
        defer {
            timing.log(Task.isCancelled ? "cancelled" : outcome, narration: current, attempt: attempts)
        }
        timing.log("manifest_preparation_started", narration: current)
        do {
            if let current, current.playable, current.firstPlayableChapter != nil {
                outcome = "cached_manifest_ready"
                return .ready(current)
            }

            if let failed = current, failed.status == .failed {
                let requestStartedAt = ContinuousClock.now
                current = try await retryNarration(failed, service: briefingService)
                timing.log("retry_command_finished", narration: current, operationStartedAt: requestStartedAt)
            } else if current == nil {
                let requestStartedAt = ContinuousClock.now
                current = try await briefingService.requestNarration(lensKey: lensKey)
                timing.log("request_command_finished", narration: current, operationStartedAt: requestStartedAt)
                try Task.checkCancellation()
            }

            guard var narration = current else {
                return .failed(AudioEpisodeServiceError.generationFailed, cachedNarration: nil)
            }
            if narration.playable, narration.firstPlayableChapter != nil {
                outcome = "manifest_ready"
                return .ready(narration)
            }
            if narration.status == .failed {
                return .failed(AudioEpisodeServiceError.generationFailed, cachedNarration: narration)
            }

            for attempt in 0..<maxAttempts {
                try await Task.sleep(nanoseconds: pollIntervalNanoseconds)
                let pollStartedAt = ContinuousClock.now
                attempts = attempt + 1
                narration = try await briefingService.fetchNarration(
                    episodeGroupID: narration.episodeGroupId
                )
                current = narration
                timing.log("manifest_poll_finished", narration: current, attempt: attempts, operationStartedAt: pollStartedAt)
                try Task.checkCancellation()
                if narration.playable, narration.firstPlayableChapter != nil {
                    outcome = "manifest_ready"
                    return .ready(narration)
                }
                if narration.status == .failed {
                    return .failed(AudioEpisodeServiceError.generationFailed, cachedNarration: narration)
                }
            }

            outcome = "timed_out"
            return .failed(AudioEpisodeServiceError.preparationTimedOut, cachedNarration: current)
        } catch let error where ClientFailure.classify(error) == .cancelled {
            outcome = "cancelled"
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

    private func finishNarrationChapter(
        after finishedTarget: NarrationTarget,
        chapterIndex: Int,
        lensKey: String,
        playbackIntentID: UUID,
        readMarks: Task<Void, Never>
    ) {
        guard self.playbackIntentID == playbackIntentID,
              case .audioEpisode(let episodeID) = finishedTarget,
              let narration = narration(for: lensKey),
              narration.chapters.indices.contains(chapterIndex),
              narration.chapters[chapterIndex].id == episodeID,
              narrationChapterIndex(for: lensKey) == chapterIndex else { return }
        guard let nextIndex = nextNarrationChapterIndex(
                afterFinishedEpisodeID: episodeID,
                chapterIndex: chapterIndex,
                for: lensKey
              ) else {
            updateSession(for: lensKey) { $0.edition = .finished(readMarks: readMarks) }
            return
        }
        Task { @MainActor [weak self] in
            await self?.playChapter(
                at: nextIndex,
                for: lensKey,
                playbackIntentID: playbackIntentID
            )
        }
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
