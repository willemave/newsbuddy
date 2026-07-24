//
//  LearningDeckService.swift
//  newsly
//

import Foundation

enum LearningDeckURLValidationError: LocalizedError, Equatable {
    case invalidURL
    case insecureRemoteURL

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            "The server returned an invalid Learning Deck URL."
        case .insecureRemoteURL:
            "The server returned an insecure Learning Deck URL."
        }
    }
}

enum LearningDeckURLValidator {
    static func validate(_ rawValue: String, apiBaseURL: URL) throws -> URL {
        guard
            let url = URL(string: rawValue),
            let scheme = url.scheme?.lowercased(),
            let host = url.host,
            let apiScheme = apiBaseURL.scheme?.lowercased(),
            let apiHost = apiBaseURL.host,
            url.user == nil,
            url.password == nil,
            url.query == nil,
            url.fragment == nil,
            hasValidSignedPath(url.path)
        else {
            throw LearningDeckURLValidationError.invalidURL
        }

        let apiIsLoopback = isLoopback(apiHost)
        if isLoopback(host) {
            guard
                apiIsLoopback,
                scheme == "http" || scheme == "https",
                effectivePort(for: url) == effectivePort(for: apiBaseURL)
            else {
                throw LearningDeckURLValidationError.insecureRemoteURL
            }
            return url
        }

        // The authenticated API response is authoritative for the public
        // viewer origin. It can legitimately be a canonical host while the
        // app is connected through an alias or an older saved API hostname.
        // WKWebView receives only this signed URL, never the API bearer token.
        guard
            scheme == "https",
            apiIsLoopback || apiScheme == "https"
        else {
            throw LearningDeckURLValidationError.insecureRemoteURL
        }
        return url
    }

    private static func hasValidSignedPath(_ path: String) -> Bool {
        let components = path.split(separator: "/", omittingEmptySubsequences: false)
        guard
            components.count == 4 || components.count == 5,
            components[0].isEmpty,
            components[1] == "learning",
            components[2] == "signed",
            !components[3].isEmpty
        else {
            return false
        }

        return components.count == 4 || components[4] == "source-notes"
    }

    private static func isLoopback(_ host: String) -> Bool {
        let normalized = host.lowercased()
            .trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
        if normalized == "localhost" || normalized == "::1" {
            return true
        }

        let octets = normalized.split(separator: ".", omittingEmptySubsequences: false)
        return octets.count == 4
            && octets.first == "127"
            && octets.allSatisfy { UInt8($0) != nil }
    }

    private static func effectivePort(for url: URL) -> Int? {
        if let port = url.port { return port }
        switch url.scheme?.lowercased() {
        case "https": return 443
        case "http": return 80
        default: return nil
        }
    }
}

protocol LearningDeckServicing: AnyObject {
    func listDecks() async throws -> LearningDeckListResponse
    func fetchDeck(id: Int) async throws -> LearningDeck
    func createDeck(
        contentId: Int?,
        newsItemId: Int?,
        url: String?,
        interestsPrompt: String?
    ) async throws -> LearningDeck
    func viewerURL(deckId: Int) async throws -> URL
    func sourceNotesURL(deckId: Int) async throws -> URL
    func enableShare(deckId: Int) async throws -> LearningDeckShareResponse
    func disableShare(deckId: Int) async throws -> LearningDeckShareResponse
    func deleteDeck(deckId: Int) async throws
}

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
        guard let apiBaseURL = URL(string: AppSettings.shared.baseURL) else {
            throw APIError.invalidURL
        }
        return try LearningDeckURLValidator.validate(response.url, apiBaseURL: apiBaseURL)
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

extension LearningDeckService: LearningDeckServicing {}
