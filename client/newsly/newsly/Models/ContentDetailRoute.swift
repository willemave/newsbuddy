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

    enum CodingKeys: String, CodingKey {
        case contentId
        case contentType
        case allContentIds
        case navigationSurface
    }

    init(
        contentId: Int,
        contentType: APIContentType,
        allContentIds: [Int],
        navigationSurface: ContentDetailNavigationSurface = .direct
    ) {
        self.contentId = contentId
        self.contentType = contentType
        self.allContentIds = allContentIds
        self.navigationSurface = navigationSurface
    }

    init(
        summary: ContentSummary,
        allContentIds: [Int],
        navigationSurface: ContentDetailNavigationSurface = .direct
    ) {
        self.contentId = summary.id
        self.contentType = summary.apiContentType ?? .article
        self.allContentIds = allContentIds
        self.navigationSurface = navigationSurface
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
    }
}
