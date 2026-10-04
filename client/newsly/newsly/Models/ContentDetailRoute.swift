//
//  ContentDetailRoute.swift
//  newsly
//
//  Created by Assistant on 3/16/26.
//

import Foundation

struct ContentDetailRoute: Hashable {
    let contentId: Int
    let contentType: APIContentType
    let allContentIds: [Int]
    let navigationSurface: ContentDetailNavigationSurface
    let initialScrollTarget: ContentDetailScrollTarget?

    init(
        contentId: Int,
        contentType: APIContentType,
        allContentIds: [Int],
        navigationSurface: ContentDetailNavigationSurface = .direct,
        initialScrollTarget: ContentDetailScrollTarget? = nil
    ) {
        self.contentId = contentId
        self.contentType = contentType
        self.allContentIds = allContentIds
        self.navigationSurface = navigationSurface
        self.initialScrollTarget = initialScrollTarget
    }

    init(
        summary: ContentSummary,
        allContentIds: [Int],
        navigationSurface: ContentDetailNavigationSurface = .direct,
        initialScrollTarget: ContentDetailScrollTarget? = nil
    ) {
        self.contentId = summary.id
        self.contentType = summary.contentType
        self.allContentIds = allContentIds
        self.navigationSurface = navigationSurface
        self.initialScrollTarget = initialScrollTarget
    }
}
