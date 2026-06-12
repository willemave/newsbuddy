//
//  Narration.swift
//  newsly
//

import Foundation

enum NarrationTarget: Hashable {
    case audioEpisode(Int)
}

typealias AudioEpisode = APIAudioEpisodeResponse

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
