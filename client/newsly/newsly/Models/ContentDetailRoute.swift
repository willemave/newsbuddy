//
//  ContentDetailRoute.swift
//  newsly
//
//  Created by Assistant on 3/16/26.
//

import Foundation

struct ContentDetailRoute: Hashable, Codable {
    let contentId: Int
    let contentType: APIContentType
    let allContentIds: [Int]
    let navigationSurface: ContentDetailNavigationSurface
    let initialScrollTarget: ContentDetailScrollTarget?

    enum CodingKeys: String, CodingKey {
        case contentId
        case contentType
        case allContentIds
        case navigationSurface
        case initialScrollTarget
    }

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

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        contentId = try container.decode(Int.self, forKey: .contentId)
        contentType = try container.decode(APIContentType.self, forKey: .contentType)
        allContentIds = try container.decode([Int].self, forKey: .allContentIds)
        navigationSurface = try container.decodeIfPresent(
            ContentDetailNavigationSurface.self,
            forKey: .navigationSurface
        ) ?? .direct
        initialScrollTarget = try container.decodeIfPresent(
            ContentDetailScrollTarget.self,
            forKey: .initialScrollTarget
        )
    }
}
