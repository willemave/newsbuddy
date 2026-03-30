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

    enum CodingKeys: String, CodingKey {
        case contentId = "content_id"
        case variant
        case kind
        case format
        case text
        case updatedAt = "updated_at"
    }
}
