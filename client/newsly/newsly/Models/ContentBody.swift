//
//  ContentBody.swift
//  newsly
//

import Foundation

struct ContentBody: Codable {
    let contentId: Int
    let variant: String
    let kind: String
    let format: String
    let text: String
    let updatedAt: String?

    init(
        contentId: Int,
        variant: String,
        kind: String,
        format: String,
        text: String,
        updatedAt: String?
    ) {
        self.contentId = contentId
        self.variant = variant
        self.kind = kind
        self.format = format
        self.text = text
        self.updatedAt = updatedAt
    }

    init(api response: APIContentBodyResponse) {
        contentId = response.contentId
        variant = response.variant
        kind = response.kind
        format = response.format
        text = response.text
        updatedAt = response.updatedAt.map(ServerDate.format)
    }

    init(from decoder: Decoder) throws {
        self.init(api: try APIContentBodyResponse(from: decoder))
    }

    enum CodingKeys: String, CodingKey {
        case contentId = "content_id"
        case variant
        case kind
        case format
        case text
        case updatedAt = "updated_at"
    }
}
