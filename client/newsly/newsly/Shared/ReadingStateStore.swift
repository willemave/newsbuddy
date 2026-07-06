//
//  ReadingStateStore.swift
//  newsly
//
//  Created by Assistant on 3/16/26.
//

import Foundation
import Observation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ReadingState")

struct ReadingState: Codable, Equatable {
    let contentId: Int
    let contentType: APIContentType
    let lastUpdated: Date
}

@MainActor
@Observable
final class ReadingStateStore {
    var current: ReadingState?

    @ObservationIgnored
    private let defaults: UserDefaults

    @ObservationIgnored
    private let storageKey: String

    init(userId: Int? = nil, defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.storageKey = if let userId {
            "currentReadingState.user.\(userId)"
        } else {
            "currentReadingState"
        }
        load()
        logger.info("[ReadingState] Store initialized")
    }

    func setCurrent(contentId: Int, type: APIContentType) {
        logger.info("[ReadingState] setCurrent | contentId=\(contentId) type=\(type.rawValue, privacy: .public)")
        let state = ReadingState(contentId: contentId, contentType: type, lastUpdated: Date())
        current = state
        persist()
    }

    func clear() {
        logger.info("[ReadingState] clear | previousContentId=\(self.current?.contentId ?? -1)")
        current = nil
        defaults.removeObject(forKey: storageKey)
    }

    private func persist() {
        guard let current else { return }
        if let data = try? JSONEncoder().encode(current) {
            defaults.set(data, forKey: storageKey)
        }
    }

    private func load() {
        guard let data = defaults.data(forKey: storageKey),
              let state = try? JSONDecoder().decode(ReadingState.self, from: data)
        else { return }
        current = state
    }
}
