//
//  LearningDeckService.swift
//  newsly
//

import Foundation

final class LearningDeckService {
    static let shared = LearningDeckService()

    private let client = APIClient.shared

    private init() {}

    func listDecks() async throws -> LearningDeckListResponse {
        try await client.request(APIEndpoints.learningDecks)
    }

    func fetchDeck(id: Int) async throws -> LearningDeck {
        try await client.request(APIEndpoints.learningDeck(id: id))
    }

    func createDeck(
        contentId: Int? = nil,
        newsItemId: Int? = nil,
        url: String? = nil,
        interestsPrompt: String? = nil
    ) async throws -> LearningDeck {
        let request = LearningDeckCreateRequest(
            contentId: contentId,
            newsItemId: newsItemId,
            url: url,
            interestsPrompt: interestsPrompt
        )
        let body = try JSONEncoder().encode(request)
        return try await client.request(
            APIEndpoints.learningDecks,
            method: "POST",
            body: body
        )
    }

    func viewerURL(deckId: Int) async throws -> URL {
        try await deckURL(endpoint: APIEndpoints.learningDeckViewerURL(id: deckId))
    }

    func sourceNotesURL(deckId: Int) async throws -> URL {
        try await deckURL(endpoint: APIEndpoints.learningDeckSourceNotesURL(id: deckId))
    }

    private func deckURL(endpoint: String) async throws -> URL {
        let response: LearningDeckURLResponse = try await client.request(
            endpoint,
            method: "POST"
        )
        guard let url = URL(string: response.url) else {
            throw APIError.invalidURL
        }
        return url
    }

    func enableShare(deckId: Int) async throws -> LearningDeckShareResponse {
        try await client.request(
            APIEndpoints.learningDeckShare(id: deckId),
            method: "POST"
        )
    }

    func disableShare(deckId: Int) async throws -> LearningDeckShareResponse {
        try await client.request(
            APIEndpoints.learningDeckShare(id: deckId),
            method: "DELETE"
        )
    }

    func deleteDeck(deckId: Int) async throws {
        try await client.requestVoid(
            APIEndpoints.learningDeck(id: deckId),
            method: "DELETE"
        )
    }
}
