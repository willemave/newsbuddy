//
//  ContentListResponse.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Foundation

typealias PaginationMetadata = APIPaginationMetadata

struct ContentListResponse: Codable {
    let contents: [ContentSummary]
    let availableDates: [String]
    let contentTypes: [String]
    let meta: PaginationMetadata

    enum CodingKeys: String, CodingKey {
        case contents
        case availableDates = "available_dates"
        case contentTypes = "content_types"
        case meta
    }

    init(
        contents: [ContentSummary],
        availableDates: [String],
        contentTypes: [String],
        meta: PaginationMetadata
    ) {
        self.contents = contents
        self.availableDates = availableDates
        self.contentTypes = contentTypes
        self.meta = meta
    }

    init(api response: APIContentListResponse) {
        self.init(
            contents: response.contents.map(ContentSummary.init(api:)),
            availableDates: response.availableDates,
            contentTypes: response.contentTypes.map(\.rawValue),
            meta: response.meta
        )
    }

    init(api response: APINewsItemListResponse) {
        self.init(
            contents: response.contents.map(ContentSummary.init(api:)),
            availableDates: response.availableDates,
            contentTypes: response.contentTypes.map(\.rawValue),
            meta: response.meta
        )
    }

    init(from decoder: Decoder) throws {
        self.init(api: try APIContentListResponse(from: decoder))
    }

    var total: Int? { meta.total }
    var nextCursor: String? { meta.nextCursor }
    var hasMore: Bool { meta.hasMore }
    var pageSize: Int { meta.pageSize }
}
