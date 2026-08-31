//
//  TweetSuggestion.swift
//  newsly
//
//  Tweet suggestion models for social sharing.
//

import Foundation

/// A single tweet suggestion from the LLM.
struct TweetSuggestion: Codable, Identifiable {
    let id: Int
    let text: String
    let styleLabel: String?

    init(id: Int, text: String, styleLabel: String?) {
        self.id = id
        self.text = text
        self.styleLabel = styleLabel
    }

    enum CodingKeys: String, CodingKey {
        case id
        case text
        case styleLabel = "style_label"
    }

    init(api response: APITweetSuggestion) {
        id = response.id
        text = response.text
        styleLabel = response.styleLabel
    }
}

/// Response containing generated tweet suggestions.
struct TweetSuggestionsResponse: Codable {
    let contentId: Int
    let creativity: Int
    let model: String
    let suggestions: [TweetSuggestion]

    init(
        contentId: Int,
        creativity: Int,
        model: String,
        suggestions: [TweetSuggestion]
    ) {
        self.contentId = contentId
        self.creativity = creativity
        self.model = model
        self.suggestions = suggestions
    }

    init(api response: APITweetSuggestionsResponse) {
        contentId = response.contentId
        creativity = response.creativity
        model = response.model
        suggestions = response.suggestions.map(TweetSuggestion.init(api:))
    }

    enum CodingKeys: String, CodingKey {
        case contentId = "content_id"
        case creativity
        case model
        case suggestions
    }
}
