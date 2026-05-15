//
//  NarrationPlaybackService.swift
//  newsly
//

import AVFoundation
import Foundation

@MainActor
final class NarrationPlaybackService: ObservableObject {
    static let shared = NarrationPlaybackService()
    nonisolated static let defaultPlaybackRate: Float = 1.0
    nonisolated static let longPressPlaybackRate: Float = 1.5

    @Published private(set) var isSpeaking = false
    @Published private(set) var isPaused = false
    @Published private(set) var playbackRate: Float
    @Published private(set) var speakingTarget: NarrationTarget?
    @Published private(set) var currentTime: TimeInterval = 0
    @Published private(set) var duration: TimeInterval = 0

    private let preferenceStore: NarrationPlaybackPreferenceStore
    private var streamPlayer: AVPlayer?
    private var streamEndObserver: NSObjectProtocol?
    private var streamFailureObserver: NSObjectProtocol?
    private var progressTimer: Timer?
    private var savedPlaybackPositions: [NarrationTarget: TimeInterval] = [:]

    private init() {
        let preferenceStore = NarrationPlaybackPreferenceStore.shared
        self.preferenceStore = preferenceStore
        self.playbackRate = preferenceStore.preferredPlaybackRate()
    }

    var playbackSpeedTitle: String {
        NarrationPlaybackSpeedOption.title(for: playbackRate)
    }

    func setPlaybackRate(_ rate: Float) {
        let normalizedRate = preferenceStore.normalizedPlaybackRate(rate)
        playbackRate = normalizedRate
        preferenceStore.savePreferredPlaybackRate(normalizedRate)
        if let streamPlayer, isSpeaking {
            streamPlayer.rate = normalizedRate
        }
    }

    func playStreamingNarration(
        for target: NarrationTarget,
        rate: Float = defaultPlaybackRate,
        fetchStreamResource: () async throws -> AuthorizedMediaResource
    ) async throws {
        setPlaybackRate(rate)

        if speakingTarget == target {
            if try resumeStreamIfNeeded(for: target) {
                return
            }
            if isSpeaking {
                return
            }
        }

        stop()

        let resource = try await fetchStreamResource()
        try playAudioStream(resource, for: target)
    }

    func playAudioStream(_ resource: AuthorizedMediaResource, for target: NarrationTarget) throws {
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
            player.automaticallyWaitsToMinimizeStalling = true

            streamPlayer = player
            observeStreamItem(item)
            speakingTarget = target
            isSpeaking = true
            isPaused = false
            currentTime = 0
            duration = 0

            if resumeTime > 0 {
                player.seek(
                    to: CMTime(seconds: resumeTime, preferredTimescale: 600),
                    toleranceBefore: .zero,
                    toleranceAfter: .zero
                )
            }
            player.playImmediately(atRate: playbackRate)
            startProgressTimer()
        } catch {
            resetPlaybackState()
            throw error
        }
    }

    func pause() {
        guard let target = speakingTarget else { return }
        if let streamPlayer {
            let currentSeconds = finiteSeconds(streamPlayer.currentTime().seconds) ?? 0
            savedPlaybackPositions[target] = currentSeconds
            currentTime = currentSeconds
            duration = streamDuration(streamPlayer) ?? duration
            streamPlayer.pause()
            isSpeaking = false
            isPaused = true
            stopProgressTimer()
            return
        }

        resetPlaybackState(clearSavedPositionFor: target)
    }

    func stop() {
        let target = speakingTarget
        streamPlayer?.pause()
        streamPlayer = nil
        resetPlaybackState(clearSavedPositionFor: target)
    }

    func seek(to progress: Double, for target: NarrationTarget) {
        guard speakingTarget == target else { return }
        let clampedProgress = min(max(progress, 0), 1)
        guard let streamPlayer, let streamDuration = streamDuration(streamPlayer) else { return }
        let nextTime = streamDuration * clampedProgress
        streamPlayer.seek(
            to: CMTime(seconds: nextTime, preferredTimescale: 600),
            toleranceBefore: .zero,
            toleranceAfter: .zero
        )
        savedPlaybackPositions[target] = nextTime
        syncProgressFromPlayer()
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
        syncProgressFromPlayer()
        startProgressTimer()
        return true
    }

    private func observeStreamItem(_ item: AVPlayerItem) {
        removeStreamObservers()
        streamEndObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.resetPlaybackState(clearSavedPositionFor: self?.speakingTarget)
            }
        }
        streamFailureObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemFailedToPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.resetPlaybackState(clearSavedPositionFor: self?.speakingTarget)
            }
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
            currentTime = finiteSeconds(streamPlayer.currentTime().seconds) ?? 0
            if let resolvedDuration = streamDuration(streamPlayer) {
                duration = resolvedDuration
            }
            return
        }
        currentTime = 0
        duration = 0
    }

    private func resetPlaybackState(clearSavedPositionFor target: NarrationTarget? = nil) {
        stopProgressTimer()
        removeStreamObservers()
        if let target {
            savedPlaybackPositions.removeValue(forKey: target)
        }
        streamPlayer = nil
        isSpeaking = false
        isPaused = false
        speakingTarget = nil
        currentTime = 0
        duration = 0
        try? AVAudioSession.sharedInstance().setActive(
            false,
            options: [.notifyOthersOnDeactivation]
        )
    }

    private func streamDuration(_ player: AVPlayer) -> TimeInterval? {
        guard let duration = player.currentItem?.duration.seconds else { return nil }
        return finiteSeconds(duration)
    }

    private func finiteSeconds(_ seconds: Double) -> TimeInterval? {
        guard seconds.isFinite, seconds > 0 else { return nil }
        return seconds
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
