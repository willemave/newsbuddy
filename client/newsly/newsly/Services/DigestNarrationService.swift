//
//  DigestNarrationService.swift
//  newsly
//

import AVFoundation
import Foundation

@MainActor
final class DigestNarrationService: NSObject, ObservableObject, AVSpeechSynthesizerDelegate {
    static let shared = DigestNarrationService()

    @Published private(set) var isSpeaking = false
    @Published private(set) var speakingDigestId: Int?

    private let synthesizer = AVSpeechSynthesizer()

    private override init() {
        super.init()
        synthesizer.delegate = self
    }

    func speak(text: String, digestId: Int) {
        let normalized = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else { return }

        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }

        let utterance = AVSpeechUtterance(string: normalized)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate * 0.95
        utterance.pitchMultiplier = 1.0
        utterance.volume = 1.0
        utterance.voice = AVSpeechSynthesisVoice(language: Locale.current.identifier)

        speakingDigestId = digestId
        isSpeaking = true
        synthesizer.speak(utterance)
    }

    func stop() {
        guard synthesizer.isSpeaking else { return }
        synthesizer.stopSpeaking(at: .immediate)
        isSpeaking = false
        speakingDigestId = nil
    }

    func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer,
        didFinish utterance: AVSpeechUtterance
    ) {
        let _ = utterance
        let _ = synthesizer
        isSpeaking = false
        speakingDigestId = nil
    }

    func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer,
        didCancel utterance: AVSpeechUtterance
    ) {
        let _ = utterance
        let _ = synthesizer
        isSpeaking = false
        speakingDigestId = nil
    }
}
