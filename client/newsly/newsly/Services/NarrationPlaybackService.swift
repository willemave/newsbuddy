//
//  NarrationPlaybackService.swift
//  newsly
//

import AVFoundation
import Foundation
import Observation
import os.log

private let narrationPlaybackLogger = Logger(subsystem: "com.newsly", category: "NarrationPlayback")
typealias NarrationPlaybackFinishedHandler = @MainActor (NarrationTarget) -> Void

private func narrationElapsedMilliseconds(since start: Date) -> Int {
    Int(Date().timeIntervalSince(start) * 1000)
}

@MainActor
@Observable
final class NarrationPlaybackProgress {
    private(set) var currentTime: TimeInterval = 0
    private(set) var duration: TimeInterval = 0

    func update(
        currentTime nextCurrentTime: TimeInterval,
        duration nextDuration: TimeInterval,
        force: Bool = false
    ) {
        let normalizedCurrentTime = normalizedSeconds(nextCurrentTime)
        let normalizedDuration = normalizedSeconds(nextDuration)
        guard force
            || displaySecond(normalizedCurrentTime) != displaySecond(currentTime)
            || displaySecond(normalizedDuration) != displaySecond(duration)
        else {
            return
        }
        currentTime = normalizedCurrentTime
        duration = normalizedDuration
    }

    func reset() {
        update(currentTime: 0, duration: 0, force: true)
    }

    private func normalizedSeconds(_ seconds: TimeInterval) -> TimeInterval {
        guard seconds.isFinite, seconds > 0 else { return 0 }
        return seconds
    }

    private func displaySecond(_ seconds: TimeInterval) -> Int {
        Int(seconds.rounded(.down))
    }
}

@MainActor
@Observable
final class NarrationPlaybackService {
    static let shared = NarrationPlaybackService(nowPlayingController: .shared)
    nonisolated static let defaultPlaybackRate: Float = 1.0
    nonisolated static let longPressPlaybackRate: Float = 1.5

    let progress = NarrationPlaybackProgress()

    private(set) var isSpeaking = false
    private(set) var isPaused = false
    private(set) var playbackRate: Float
    private(set) var speakingTarget: NarrationTarget?

    @ObservationIgnored
    private let preferenceStore: NarrationPlaybackPreferenceStore

    @ObservationIgnored
    private let nowPlayingController: NarrationNowPlayingController?

    @ObservationIgnored
    private var streamPlayer: AVPlayer?

    @ObservationIgnored
    private var streamEndObserver: NSObjectProtocol?

    @ObservationIgnored
    private var streamFailureObserver: NSObjectProtocol?

    @ObservationIgnored
    private var streamItemStatusObserver: NSKeyValueObservation?

    @ObservationIgnored
    private var streamTimeControlObserver: NSKeyValueObservation?

    @ObservationIgnored
    private var progressTimer: Timer?

    @ObservationIgnored
    private var savedPlaybackPositions: [NarrationTarget: TimeInterval] = [:]

    @ObservationIgnored
    private var playbackStartedAt: Date?

    @ObservationIgnored
    private var playbackItemReadyLogged = false

    @ObservationIgnored
    private var playbackTimeControlPlayingLogged = false

    @ObservationIgnored
    private var playbackTimeControlWaitingLogged = false

    @ObservationIgnored
    private var playbackFirstProgressLogged = false

    @ObservationIgnored
    private var playbackRequestGeneration = 0

    @ObservationIgnored
    private var playbackSessionID: UUID?

    @ObservationIgnored
    private var playbackFinishedHandler: NarrationPlaybackFinishedHandler?

    @ObservationIgnored
    private var interruptionObserver: NSObjectProtocol?

    @ObservationIgnored
    private var routeChangeObserver: NSObjectProtocol?

    @ObservationIgnored
    private var interruptedPlaybackSessionID: UUID?

    init(
        preferenceStore: NarrationPlaybackPreferenceStore = .shared,
        nowPlayingController: NarrationNowPlayingController? = nil
    ) {
        self.preferenceStore = preferenceStore
        self.nowPlayingController = nowPlayingController
        self.playbackRate = preferenceStore.preferredPlaybackRate()
        if nowPlayingController != nil {
            observeAudioSession()
        }
    }

    deinit {
        if let interruptionObserver {
            NotificationCenter.default.removeObserver(interruptionObserver)
        }
        if let routeChangeObserver {
            NotificationCenter.default.removeObserver(routeChangeObserver)
        }
    }

    var playbackSpeedTitle: String {
        NarrationPlaybackSpeedOption.title(for: playbackRate)
    }

    var currentTime: TimeInterval {
        progress.currentTime
    }

    var duration: TimeInterval {
        progress.duration
    }

    func setPlaybackRate(_ rate: Float) {
        let normalizedRate = preferenceStore.normalizedPlaybackRate(rate)
        playbackRate = normalizedRate
        preferenceStore.savePreferredPlaybackRate(normalizedRate)
        if let streamPlayer, isSpeaking {
            streamPlayer.rate = normalizedRate
        }
        updateNowPlaying()
    }

    func playStreamingNarration(
        for target: NarrationTarget,
        metadata: NarrationPlaybackMetadata? = nil,
        remotePrevious: (@MainActor () -> Void)? = nil,
        remoteNext: (@MainActor () -> Void)? = nil,
        onFinished: NarrationPlaybackFinishedHandler? = nil,
        fetchStreamResource: () async throws -> AuthorizedMediaResource
    ) async throws {
        try await playStreamingNarration(
            for: target,
            rate: playbackRate,
            metadata: metadata,
            remotePrevious: remotePrevious,
            remoteNext: remoteNext,
            onFinished: onFinished,
            fetchStreamResource: fetchStreamResource
        )
    }

    func playStreamingNarration(
        for target: NarrationTarget,
        rate: Float,
        metadata: NarrationPlaybackMetadata? = nil,
        remotePrevious: (@MainActor () -> Void)? = nil,
        remoteNext: (@MainActor () -> Void)? = nil,
        onFinished: NarrationPlaybackFinishedHandler? = nil,
        fetchStreamResource: () async throws -> AuthorizedMediaResource
    ) async throws {
        let startedAt = Date()
        narrationPlaybackLogger.info(
            "Streaming narration requested | target=\(String(describing: target), privacy: .public) rate=\(rate)"
        )
        setPlaybackRate(rate)

        if speakingTarget == target {
            playbackFinishedHandler = onFinished
            if try resumeStreamIfNeeded(for: target) {
                activateNowPlaying(
                    metadata: metadata,
                    target: target,
                    remotePrevious: remotePrevious,
                    remoteNext: remoteNext
                )
                narrationPlaybackLogger.info(
                    "Streaming narration resumed | target=\(String(describing: target), privacy: .public) elapsedMs=\(narrationElapsedMilliseconds(since: startedAt))"
                )
                return
            }
            if isSpeaking {
                activateNowPlaying(
                    metadata: metadata,
                    target: target,
                    remotePrevious: remotePrevious,
                    remoteNext: remoteNext
                )
                narrationPlaybackLogger.info(
                    "Streaming narration already active | target=\(String(describing: target), privacy: .public) elapsedMs=\(narrationElapsedMilliseconds(since: startedAt))"
                )
                return
            }
        }

        stop()
        let requestGeneration = playbackRequestGeneration

        do {
            let resource = try await fetchStreamResource()
            guard requestGeneration == playbackRequestGeneration else {
                throw CancellationError()
            }
            narrationPlaybackLogger.info(
                "Streaming narration resource ready | target=\(String(describing: target), privacy: .public) elapsedMs=\(narrationElapsedMilliseconds(since: startedAt))"
            )
            try playAudioStream(
                resource,
                for: target,
                metadata: metadata,
                remotePrevious: remotePrevious,
                remoteNext: remoteNext,
                onFinished: onFinished
            )
        } catch where ClientFailure.classify(error) == .cancelled {
            throw CancellationError()
        } catch {
            narrationPlaybackLogger.error(
                "Streaming narration failed before playback | target=\(String(describing: target), privacy: .public) elapsedMs=\(narrationElapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
            throw error
        }
    }

    @discardableResult
    func playAudioStream(
        _ resource: AuthorizedMediaResource,
        for target: NarrationTarget,
        metadata: NarrationPlaybackMetadata? = nil,
        remotePrevious: (@MainActor () -> Void)? = nil,
        remoteNext: (@MainActor () -> Void)? = nil,
        onFinished: NarrationPlaybackFinishedHandler? = nil
    ) throws -> AVPlayerItem {
        let startedAt = Date()
        let resumeTime = savedPlaybackPositions[target] ?? 0
        stop()
        do {
            try configurePlaybackSession()

            let asset = AVURLAsset(
                url: resource.url,
                options: ["AVURLAssetHTTPHeaderFieldsKey": resource.headers]
            )
            let item = AVPlayerItem(asset: asset)
            let player = AVPlayer(playerItem: item)
            player.automaticallyWaitsToMinimizeStalling = false
            let playbackSessionID = UUID()

            streamPlayer = player
            self.playbackSessionID = playbackSessionID
            playbackFinishedHandler = onFinished
            speakingTarget = target
            isSpeaking = true
            isPaused = false
            progress.reset()
            playbackStartedAt = startedAt
            playbackItemReadyLogged = false
            playbackTimeControlPlayingLogged = false
            playbackTimeControlWaitingLogged = false
            playbackFirstProgressLogged = false
            observeStreamItem(
                item,
                playbackSessionID: playbackSessionID,
                target: target
            )
            observeStreamPlayer(player)

            if resumeTime > 0 {
                player.seek(
                    to: CMTime(seconds: resumeTime, preferredTimescale: 600),
                    toleranceBefore: .zero,
                    toleranceAfter: .zero
                )
            }
            player.playImmediately(atRate: playbackRate)
            activateNowPlaying(
                metadata: metadata,
                target: target,
                remotePrevious: remotePrevious,
                remoteNext: remoteNext
            )
            narrationPlaybackLogger.info(
                "AVPlayer stream play called | target=\(String(describing: target), privacy: .public) elapsedMs=\(narrationElapsedMilliseconds(since: startedAt)) resumeSeconds=\(resumeTime, privacy: .public) headerCount=\(resource.headers.count) minimizeStalling=\(player.automaticallyWaitsToMinimizeStalling)"
            )
            startProgressTimer()
            return item
        } catch {
            narrationPlaybackLogger.error(
                "AVPlayer stream setup failed | target=\(String(describing: target), privacy: .public) elapsedMs=\(narrationElapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
            resetPlaybackState()
            throw error
        }
    }

    func pause() {
        guard let target = speakingTarget else { return }
        if let streamPlayer {
            let currentSeconds = finiteSeconds(streamPlayer.currentTime().seconds) ?? 0
            savedPlaybackPositions[target] = currentSeconds
            progress.update(
                currentTime: currentSeconds,
                duration: streamDuration(streamPlayer) ?? duration,
                force: true
            )
            streamPlayer.pause()
            isSpeaking = false
            isPaused = true
            stopProgressTimer()
            updateNowPlaying()
            narrationPlaybackLogger.info(
                "Streaming narration paused | target=\(String(describing: target), privacy: .public) currentSeconds=\(currentSeconds, privacy: .public)"
            )
            return
        }

        resetPlaybackState(clearSavedPositionFor: target)
    }

    func stop() {
        playbackRequestGeneration += 1
        let target = speakingTarget
        if let target {
            narrationPlaybackLogger.info(
                "Streaming narration stopped | target=\(String(describing: target), privacy: .public) currentSeconds=\(self.currentTime, privacy: .public)"
            )
        }
        streamPlayer?.pause()
        streamPlayer = nil
        resetPlaybackState(clearSavedPositionFor: target)
    }

    func seek(to progress: Double, for target: NarrationTarget) {
        let clampedProgress = min(max(progress, 0), 1)
        guard let streamPlayer, let streamDuration = streamDuration(streamPlayer) else { return }
        seek(toTime: streamDuration * clampedProgress, for: target)
    }

    private func seek(toTime time: TimeInterval, for target: NarrationTarget) {
        guard speakingTarget == target,
              let streamPlayer,
              let streamDuration = streamDuration(streamPlayer) else { return }
        let nextTime = min(max(time, 0), streamDuration)
        streamPlayer.seek(
            to: CMTime(seconds: nextTime, preferredTimescale: 600),
            toleranceBefore: .zero,
            toleranceAfter: .zero
        )
        savedPlaybackPositions[target] = nextTime
        progress.update(currentTime: nextTime, duration: streamDuration, force: true)
        updateNowPlaying()
    }

    private func configurePlaybackSession() throws {
        let audioSession = AVAudioSession.sharedInstance()
        try audioSession.setCategory(.playback, mode: .default, options: [.duckOthers])
        try audioSession.setActive(true)
    }

    private func resumeStreamIfNeeded(for target: NarrationTarget) throws -> Bool {
        guard isPaused, let streamPlayer else { return false }
        try configurePlaybackSession()
        streamPlayer.playImmediately(atRate: playbackRate)
        speakingTarget = target
        isSpeaking = true
        isPaused = false
        playbackStartedAt = Date()
        playbackFirstProgressLogged = false
        syncProgressFromPlayer()
        startProgressTimer()
        updateNowPlaying()
        return true
    }

    private func observeStreamItem(
        _ item: AVPlayerItem,
        playbackSessionID: UUID,
        target: NarrationTarget
    ) {
        removeStreamObservers()
        streamEndObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                await self?.handleStreamEnded(
                    item: item,
                    playbackSessionID: playbackSessionID,
                    target: target
                )
            }
        }
        streamFailureObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemFailedToPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.handleStreamFailure(
                    item: item,
                    playbackSessionID: playbackSessionID,
                    target: target
                )
            }
        }
        streamItemStatusObserver = item.observe(\.status, options: [.new]) { [weak self] item, _ in
            let isReady = item.status == .readyToPlay
            let isFailed = item.status == .failed
            let statusDescription = streamItemStatusDescription(item.status)
            let errorDescription = item.error?.localizedDescription
            Task { @MainActor [weak self] in
                self?.logStreamItemStatus(
                    isReady: isReady,
                    isFailed: isFailed,
                    statusDescription: statusDescription,
                    errorDescription: errorDescription
                )
            }
        }
    }

    private func handleStreamEnded(
        item: AVPlayerItem,
        playbackSessionID: UUID,
        target: NarrationTarget
    ) async {
        guard self.playbackSessionID == playbackSessionID,
              streamPlayer?.currentItem === item,
              speakingTarget == target else {
            return
        }
        narrationPlaybackLogger.info(
            "Streaming narration reached end | target=\(String(describing: target), privacy: .public)"
        )
        let finishedHandler = playbackFinishedHandler
        resetPlaybackState(clearSavedPositionFor: target)
        finishedHandler?(target)
        await recordPlaybackFinished(for: target)
    }

    private func handleStreamFailure(
        item: AVPlayerItem,
        playbackSessionID: UUID,
        target: NarrationTarget
    ) {
        guard self.playbackSessionID == playbackSessionID,
              streamPlayer?.currentItem === item,
              speakingTarget == target else {
            return
        }
        narrationPlaybackLogger.error(
            "Streaming narration player failed | target=\(String(describing: target), privacy: .public)"
        )
        resetPlaybackState(clearSavedPositionFor: target)
    }

    private func observeStreamPlayer(_ player: AVPlayer) {
        streamTimeControlObserver?.invalidate()
        streamTimeControlObserver = player.observe(\.timeControlStatus, options: [.new]) { [weak self] player, _ in
            let isPlaying = player.timeControlStatus == .playing
            let isWaiting = player.timeControlStatus == .waitingToPlayAtSpecifiedRate
            let statusDescription = streamTimeControlStatusDescription(player.timeControlStatus)
            let waitingReason = player.reasonForWaitingToPlay?.rawValue
            Task { @MainActor [weak self] in
                self?.logStreamTimeControlStatus(
                    isPlaying: isPlaying,
                    isWaiting: isWaiting,
                    statusDescription: statusDescription,
                    waitingReason: waitingReason
                )
            }
        }
    }

    private func logStreamItemStatus(
        isReady: Bool,
        isFailed: Bool,
        statusDescription: String,
        errorDescription: String?
    ) {
        guard let playbackStartedAt else { return }
        if isReady, !playbackItemReadyLogged {
            playbackItemReadyLogged = true
            narrationPlaybackLogger.info(
                "AVPlayer item ready | target=\(String(describing: self.speakingTarget), privacy: .public) elapsedMs=\(narrationElapsedMilliseconds(since: playbackStartedAt)) status=\(statusDescription, privacy: .public)"
            )
        } else if isFailed {
            narrationPlaybackLogger.error(
                "AVPlayer item status failed | target=\(String(describing: self.speakingTarget), privacy: .public) elapsedMs=\(narrationElapsedMilliseconds(since: playbackStartedAt)) error=\(errorDescription ?? "unknown", privacy: .public)"
            )
        }
    }

    private func logStreamTimeControlStatus(
        isPlaying: Bool,
        isWaiting: Bool,
        statusDescription: String,
        waitingReason: String?
    ) {
        guard let playbackStartedAt else { return }
        if isPlaying, !playbackTimeControlPlayingLogged {
            playbackTimeControlPlayingLogged = true
            narrationPlaybackLogger.info(
                "AVPlayer time control playing | target=\(String(describing: self.speakingTarget), privacy: .public) elapsedMs=\(narrationElapsedMilliseconds(since: playbackStartedAt)) status=\(statusDescription, privacy: .public)"
            )
        } else if isWaiting, !playbackTimeControlWaitingLogged {
            playbackTimeControlWaitingLogged = true
            narrationPlaybackLogger.info(
                "AVPlayer time control waiting | target=\(String(describing: self.speakingTarget), privacy: .public) elapsedMs=\(narrationElapsedMilliseconds(since: playbackStartedAt)) reason=\(waitingReason ?? "unknown", privacy: .public)"
            )
        }
    }

    private func removeStreamObservers() {
        if let streamEndObserver {
            NotificationCenter.default.removeObserver(streamEndObserver)
            self.streamEndObserver = nil
        }
        if let streamFailureObserver {
            NotificationCenter.default.removeObserver(streamFailureObserver)
            self.streamFailureObserver = nil
        }
        streamItemStatusObserver?.invalidate()
        streamItemStatusObserver = nil
        streamTimeControlObserver?.invalidate()
        streamTimeControlObserver = nil
    }

    private func startProgressTimer() {
        stopProgressTimer()
        progressTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                self.syncProgressFromPlayer()
            }
        }
    }

    private func stopProgressTimer() {
        progressTimer?.invalidate()
        progressTimer = nil
    }

    private func syncProgressFromPlayer() {
        if let streamPlayer {
            let currentSeconds = finiteSeconds(streamPlayer.currentTime().seconds) ?? 0
            if currentSeconds > 0,
               !playbackFirstProgressLogged,
               let playbackStartedAt {
                playbackFirstProgressLogged = true
                narrationPlaybackLogger.info(
                    "Streaming narration first playback progress | target=\(String(describing: self.speakingTarget), privacy: .public) elapsedMs=\(narrationElapsedMilliseconds(since: playbackStartedAt)) currentSeconds=\(currentSeconds, privacy: .public)"
                )
            }
            progress.update(
                currentTime: currentSeconds,
                duration: streamDuration(streamPlayer) ?? duration
            )
            updateNowPlaying()
            return
        }
        progress.reset()
    }

    private func resetPlaybackState(clearSavedPositionFor target: NarrationTarget? = nil) {
        stopProgressTimer()
        removeStreamObservers()
        if let target {
            savedPlaybackPositions.removeValue(forKey: target)
        }
        streamPlayer = nil
        playbackSessionID = nil
        playbackFinishedHandler = nil
        isSpeaking = false
        isPaused = false
        speakingTarget = nil
        progress.reset()
        playbackStartedAt = nil
        playbackItemReadyLogged = false
        playbackTimeControlPlayingLogged = false
        playbackTimeControlWaitingLogged = false
        playbackFirstProgressLogged = false
        interruptedPlaybackSessionID = nil
        nowPlayingController?.clear()
        try? AVAudioSession.sharedInstance().setActive(
            false,
            options: [.notifyOthersOnDeactivation]
        )
    }

    private func recordPlaybackFinished(for target: NarrationTarget?) async {
        guard let target, case let .audioEpisode(episodeID) = target else { return }
        do {
            try await AudioEpisodeService.shared.markPlaybackFinished(id: episodeID)
        } catch {
            narrationPlaybackLogger.error(
                "Playback completion failed | episodeId=\(episodeID) error=\(error.localizedDescription, privacy: .private)"
            )
        }
    }

    private func streamDuration(_ player: AVPlayer) -> TimeInterval? {
        guard let duration = player.currentItem?.duration.seconds else { return nil }
        return finiteSeconds(duration)
    }

    private func finiteSeconds(_ seconds: Double) -> TimeInterval? {
        guard seconds.isFinite, seconds > 0 else { return nil }
        return seconds
    }

    private func updateNowPlaying() {
        nowPlayingController?.update(
            duration: duration,
            elapsed: currentTime,
            currentRate: isSpeaking ? playbackRate : 0,
            defaultRate: playbackRate
        )
    }

    private func activateNowPlaying(
        metadata: NarrationPlaybackMetadata?,
        target: NarrationTarget,
        remotePrevious: (@MainActor () -> Void)?,
        remoteNext: (@MainActor () -> Void)?
    ) {
        guard let metadata else { return }
        nowPlayingController?.activate(
            metadata: metadata,
            duration: duration,
            elapsed: currentTime,
            currentRate: isSpeaking ? playbackRate : 0,
            defaultRate: playbackRate,
            actions: NarrationRemoteCommandActions(
                play: { [weak self] in
                    _ = try? self?.resumeStreamIfNeeded(for: target)
                },
                pause: { [weak self] in self?.pause() },
                seek: { [weak self] position in self?.seek(toTime: position, for: target) },
                previous: remotePrevious,
                next: remoteNext
            )
        )
    }

    private func observeAudioSession() {
        let center = NotificationCenter.default
        interruptionObserver = center.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: AVAudioSession.sharedInstance(),
            queue: .main
        ) { [weak self] notification in
            Task { @MainActor [weak self] in
                self?.handleAudioInterruption(notification)
            }
        }
        routeChangeObserver = center.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: AVAudioSession.sharedInstance(),
            queue: .main
        ) { [weak self] notification in
            Task { @MainActor [weak self] in
                self?.handleAudioRouteChange(notification)
            }
        }
    }

    private func handleAudioInterruption(_ notification: Notification) {
        guard let rawType = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: rawType) else { return }
        switch type {
        case .began:
            interruptedPlaybackSessionID = isSpeaking ? playbackSessionID : nil
            if interruptedPlaybackSessionID != nil {
                pause()
            }
        case .ended:
            defer { interruptedPlaybackSessionID = nil }
            guard let rawOptions = notification.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt,
                  AVAudioSession.InterruptionOptions(rawValue: rawOptions).contains(.shouldResume),
                  interruptedPlaybackSessionID == playbackSessionID,
                  let target = speakingTarget else { return }
            _ = try? resumeStreamIfNeeded(for: target)
        @unknown default:
            break
        }
    }

    private func handleAudioRouteChange(_ notification: Notification) {
        guard let rawReason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
              AVAudioSession.RouteChangeReason(rawValue: rawReason) == .oldDeviceUnavailable else {
            return
        }
        pause()
    }
}

private func streamItemStatusDescription(_ status: AVPlayerItem.Status) -> String {
    switch status {
    case .unknown:
        return "unknown"
    case .readyToPlay:
        return "readyToPlay"
    case .failed:
        return "failed"
    @unknown default:
        return "unknownDefault"
    }
}

private func streamTimeControlStatusDescription(_ status: AVPlayer.TimeControlStatus) -> String {
    switch status {
    case .paused:
        return "paused"
    case .waitingToPlayAtSpecifiedRate:
        return "waitingToPlayAtSpecifiedRate"
    case .playing:
        return "playing"
    @unknown default:
        return "unknownDefault"
    }
}

final class NarrationPlaybackPreferenceStore {
    static let shared = NarrationPlaybackPreferenceStore()

    private let defaults: UserDefaults
    private let storageKey: String

    init(
        defaults: UserDefaults = SharedContainer.userDefaults,
        storageKey: String = "preferredNarrationPlaybackRate"
    ) {
        self.defaults = defaults
        self.storageKey = storageKey
    }

    func preferredPlaybackRate() -> Float {
        guard let storedRate = defaults.object(forKey: storageKey) as? NSNumber else {
            return NarrationPlaybackService.defaultPlaybackRate
        }
        return normalizedPlaybackRate(storedRate.floatValue)
    }

    func savePreferredPlaybackRate(_ rate: Float) {
        defaults.set(normalizedPlaybackRate(rate), forKey: storageKey)
    }

    func normalizedPlaybackRate(_ rate: Float) -> Float {
        NarrationPlaybackSpeedOption.option(for: rate)?.rate
            ?? NarrationPlaybackService.defaultPlaybackRate
    }
}
