//
//  NarrationPlaybackService.swift
//  newsly
//

import AVFoundation
import Foundation

@MainActor
final class NarrationPlaybackService: NSObject, ObservableObject, @preconcurrency AVAudioPlayerDelegate, @preconcurrency AVSpeechSynthesizerDelegate {
    static let shared = NarrationPlaybackService()
    nonisolated static let defaultPlaybackRate: Float = 1.0
    nonisolated static let longPressPlaybackRate: Float = 1.5

    @Published private(set) var isSpeaking = false
    @Published private(set) var isPaused = false
    @Published private(set) var playbackRate: Float
    @Published private(set) var speakingTarget: NarrationTarget?
    @Published private(set) var currentTime: TimeInterval = 0
    @Published private(set) var duration: TimeInterval = 0

    private let synthesizer = AVSpeechSynthesizer()
    private let preferenceStore: NarrationPlaybackPreferenceStore
    private var audioPlayer: AVAudioPlayer?
    private var progressTimer: Timer?
    private var savedPlaybackPositions: [NarrationTarget: TimeInterval] = [:]
    private var cachedAudioByTarget: [NarrationTarget: Data] = [:]
    private var cachedTextByTarget: [NarrationTarget: String] = [:]
    private var cacheOrder: [NarrationTarget] = []
    private let maxCachedTargets = 12

    private override init() {
        let preferenceStore = NarrationPlaybackPreferenceStore.shared
        self.preferenceStore = preferenceStore
        self.playbackRate = preferenceStore.preferredPlaybackRate()
        super.init()
        synthesizer.delegate = self
    }

    var playbackSpeedTitle: String {
        NarrationPlaybackSpeedOption.title(for: playbackRate)
    }

    func setPlaybackRate(_ rate: Float) {
        let normalizedRate = preferenceStore.normalizedPlaybackRate(rate)
        playbackRate = normalizedRate
        preferenceStore.savePreferredPlaybackRate(normalizedRate)
        if let audioPlayer {
            audioPlayer.enableRate = true
            audioPlayer.rate = normalizedRate
        }
    }

    func playNarration(
        for target: NarrationTarget,
        rate: Float = defaultPlaybackRate,
        fetchAudio: () async throws -> Data,
        fetchNarrationText: () async throws -> String
    ) async throws {
        setPlaybackRate(rate)

        if speakingTarget == target {
            if try resumeAudioIfNeeded(for: target) {
                return
            }
            if isSpeaking {
                return
            }
        }

        stop()

        if playCachedAudio(for: target) {
            return
        }

        do {
            let audioData = try await fetchAudio()
            try playAudio(audioData, for: target)
        } catch {
            let narrationText: String
            if let cachedText = cachedTextByTarget[target] {
                narrationText = cachedText
            } else {
                narrationText = try await fetchNarrationText()
                cacheText(narrationText, for: target)
            }
            speak(text: narrationText, for: target)
        }
    }

    func playCachedAudio(for target: NarrationTarget) -> Bool {
        guard let audioData = cachedAudioByTarget[target] else { return false }
        do {
            try playAudio(audioData, for: target)
            return true
        } catch {
            removeCachedAudio(for: target)
            return false
        }
    }

    func playAudio(_ audioData: Data, for target: NarrationTarget) throws {
        guard !audioData.isEmpty else {
            throw NSError(
                domain: "NarrationPlaybackService",
                code: 2,
                userInfo: [NSLocalizedDescriptionKey: "Narration audio was empty."]
            )
        }

        let resumeTime = savedPlaybackPositions[target] ?? 0
        stop()
        cacheAudio(audioData, for: target)
        do {
            try configurePlaybackSession()

            let player = try AVAudioPlayer(data: audioData)
            player.delegate = self
            player.enableRate = true
            player.rate = playbackRate
            player.prepareToPlay()
            if resumeTime > 0, resumeTime < max(0, player.duration - 1) {
                player.currentTime = resumeTime
            }
            guard player.play() else {
                throw NSError(
                    domain: "NarrationPlaybackService",
                    code: 1,
                    userInfo: [
                        NSLocalizedDescriptionKey: "Failed to start narration audio playback."
                    ]
                )
            }

            audioPlayer = player
            speakingTarget = target
            isSpeaking = true
            isPaused = false
            syncProgressFromPlayer()
            startProgressTimer()
        } catch {
            resetPlaybackState()
            throw error
        }
    }

    func speak(text: String, for target: NarrationTarget) {
        let normalized = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else { return }

        stop()
        cacheText(normalized, for: target)

        let utterance = AVSpeechUtterance(string: normalized)
        utterance.rate = min(
            AVSpeechUtteranceMaximumSpeechRate,
            AVSpeechUtteranceDefaultSpeechRate * (0.95 * playbackRate)
        )
        utterance.pitchMultiplier = 1.0
        utterance.volume = 1.0
        utterance.voice = AVSpeechSynthesisVoice(language: Locale.current.identifier)

        speakingTarget = target
        isSpeaking = true
        isPaused = false
        currentTime = 0
        duration = 0
        synthesizer.speak(utterance)
    }

    func pause() {
        guard let target = speakingTarget else { return }
        if let audioPlayer {
            savedPlaybackPositions[target] = audioPlayer.currentTime
            currentTime = audioPlayer.currentTime
            duration = audioPlayer.duration
            audioPlayer.pause()
            isSpeaking = false
            isPaused = true
            stopProgressTimer()
            return
        }

        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
        resetPlaybackState(clearSavedPositionFor: target)
    }

    func stop() {
        let target = speakingTarget
        if audioPlayer?.isPlaying == true {
            audioPlayer?.stop()
        }
        audioPlayer = nil
        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
        resetPlaybackState(clearSavedPositionFor: target)
    }

    func seek(to progress: Double, for target: NarrationTarget) {
        guard speakingTarget == target, let audioPlayer else { return }
        let clampedProgress = min(max(progress, 0), 1)
        let nextTime = audioPlayer.duration * clampedProgress
        audioPlayer.currentTime = nextTime
        savedPlaybackPositions[target] = nextTime
        syncProgressFromPlayer()
    }

    func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer,
        didFinish utterance: AVSpeechUtterance
    ) {
        let _ = utterance
        let _ = synthesizer
        resetPlaybackState()
    }

    func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer,
        didCancel utterance: AVSpeechUtterance
    ) {
        let _ = utterance
        let _ = synthesizer
        resetPlaybackState()
    }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        let _ = player
        let _ = flag
        resetPlaybackState(clearSavedPositionFor: speakingTarget)
    }

    func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        let _ = player
        let _ = error
        resetPlaybackState(clearSavedPositionFor: speakingTarget)
    }

    private func cacheAudio(_ audioData: Data, for target: NarrationTarget) {
        cachedAudioByTarget[target] = audioData
        touchCache(target)
    }

    private func cacheText(_ text: String, for target: NarrationTarget) {
        cachedTextByTarget[target] = text
        touchCache(target)
    }

    private func touchCache(_ target: NarrationTarget) {
        cacheOrder.removeAll { $0 == target }
        cacheOrder.append(target)
        while cacheOrder.count > maxCachedTargets {
            let evictedTarget = cacheOrder.removeFirst()
            cachedAudioByTarget.removeValue(forKey: evictedTarget)
            cachedTextByTarget.removeValue(forKey: evictedTarget)
        }
    }

    private func removeCachedAudio(for target: NarrationTarget) {
        cachedAudioByTarget.removeValue(forKey: target)
        cacheOrder.removeAll { $0 == target }
    }

    private func configurePlaybackSession() throws {
        let audioSession = AVAudioSession.sharedInstance()
        try audioSession.setCategory(.playback, mode: .default, options: [.duckOthers])
        try audioSession.setActive(true)
    }

    private func resumeAudioIfNeeded(for target: NarrationTarget) throws -> Bool {
        guard isPaused, let audioPlayer else { return false }
        try configurePlaybackSession()
        audioPlayer.enableRate = true
        audioPlayer.rate = playbackRate
        guard audioPlayer.play() else {
            throw NSError(
                domain: "NarrationPlaybackService",
                code: 1,
                userInfo: [
                    NSLocalizedDescriptionKey: "Failed to resume narration audio playback."
                ]
            )
        }
        speakingTarget = target
        isSpeaking = true
        isPaused = false
        syncProgressFromPlayer()
        startProgressTimer()
        return true
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
        guard let audioPlayer else {
            currentTime = 0
            duration = 0
            return
        }
        currentTime = audioPlayer.currentTime
        duration = audioPlayer.duration
    }

    private func resetPlaybackState(clearSavedPositionFor target: NarrationTarget? = nil) {
        stopProgressTimer()
        if let target {
            savedPlaybackPositions.removeValue(forKey: target)
        }
        audioPlayer = nil
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
