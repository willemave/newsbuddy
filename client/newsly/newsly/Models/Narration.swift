//
//  Narration.swift
//  newsly
//

import Foundation

enum NarrationTarget: Hashable {
    case audioEpisode(Int)
}

typealias AudioEpisode = APIAudioEpisodeResponse
typealias BriefingNarration = APIBriefingNarrationResponse

typealias AudioEpisodeShareResponse = APIAudioEpisodeShareResponse

extension APIAudioEpisodeResponse: Identifiable {}

extension APIAudioEpisodeResponse {
    var isGenerating: Bool {
        status == .pending || status == .processing
    }

    var isCompleted: Bool {
        status == .completed
    }

    var isFailed: Bool {
        status == .failed
    }
}

extension APIBriefingNarrationResponse {
    var isGenerating: Bool {
        status == .pending || status == .processing
    }

    var firstPlayableChapter: AudioEpisode? {
        guard let first = chapters.first, first.isCompleted else { return nil }
        return first
    }
}
