//
//  ReadingStateStore.swift
//  newsly
//
//  Created by Assistant on 3/16/26.
//

import Foundation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ReadingState")

struct ReadingState: Codable, Equatable {
    let contentId: Int
    let contentType: ContentType
    let lastUpdated: Date
}

/// Notification posted when content is marked as read from detail view
extension Notification.Name {
    static let contentMarkedAsRead = Notification.Name("contentMarkedAsRead")
}

@MainActor
final class ReadingStateStore: ObservableObject {
    @Published var current: ReadingState?

    private let defaults: UserDefaults
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

    func setCurrent(contentId: Int, type: ContentType) {
        logger.info("[ReadingState] setCurrent | contentId=\(contentId) type=\(type.rawValue, privacy: .public)")
        let state = ReadingState(contentId: contentId, contentType: type, lastUpdated: Date())
        current = state
        persist()
    }

    func markAsRead(contentId: Int, contentType: ContentType) {
        logger.info("[ReadingState] markAsRead called | contentId=\(contentId) type=\(contentType.rawValue, privacy: .public)")
        // Post notification so list views can update their local state
        NotificationCenter.default.post(
            name: .contentMarkedAsRead,
            object: nil,
            userInfo: ["contentId": contentId, "contentType": contentType.rawValue]
        )
        logger.debug("[ReadingState] Posted contentMarkedAsRead notification | contentId=\(contentId)")
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
