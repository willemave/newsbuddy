//
//  ContentDetailNavigationContext.swift
//  newsly
//

import Foundation

enum ContentDetailNavigationSurface: String, Hashable {
    case direct
    case recentlyRead = "recently_read"
    case savedLibrary = "saved_library"
    case search
    case briefing
}

enum ContentDetailScrollTarget: String, Hashable {
    case comments
}

struct ContentDetailNavigationContext {
    let initialContentId: Int
    let initialContentType: APIContentType?
    let contentIds: [Int]
    let surface: ContentDetailNavigationSurface
    let initialScrollTarget: ContentDetailScrollTarget?

    init(
        initialContentId: Int,
        initialContentType: APIContentType?,
        contentIds: [Int],
        surface: ContentDetailNavigationSurface,
        initialScrollTarget: ContentDetailScrollTarget? = nil
    ) {
        self.initialContentId = initialContentId
        self.initialContentType = initialContentType
        self.contentIds = contentIds.contains(initialContentId) ? contentIds : [initialContentId]
        self.surface = surface
        self.initialScrollTarget = initialScrollTarget
    }

    var initialIndex: Int {
        contentIds.firstIndex(of: initialContentId) ?? contentIds.startIndex
    }

    func contentId(at index: Int) -> Int? {
        guard contentIds.indices.contains(index) else { return nil }
        return contentIds[index]
    }
}
