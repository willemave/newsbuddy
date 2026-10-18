import Foundation

protocol NewsFilterServicing {
    func load() async throws -> APINewsFilterPreference
    func save(revision: Int, text: String, enabled: Bool) async throws -> APINewsFilterPreference
    func preview(text: String) async throws -> APINewsFilterPreviewResponse
    func news(filtered: Bool, cursor: String?) async throws -> APINewsFilterNewsResponse
}

final class NewsFilterService: NewsFilterServicing {
    private let client: APIClient
    init(client: APIClient) { self.client = client }

    func load() async throws -> APINewsFilterPreference {
        try await client.request(APIEndpoints.newsFilter, recoveryPolicy: .safeRead)
    }

    func save(revision: Int, text: String, enabled: Bool) async throws -> APINewsFilterPreference {
        let request = APIUpdateNewsFilterRequest(expectedRevision: revision, text: text, enabled: enabled)
        return try await client.request(APIEndpoints.newsFilter, method: .put, body: JSONEncoder().encode(request))
    }

    func preview(text: String) async throws -> APINewsFilterPreviewResponse {
        try await client.request(APIEndpoints.newsFilterPreview, method: .post,
                                 body: JSONEncoder().encode(APINewsFilterPreviewRequest(text: text)))
    }

    func news(filtered: Bool, cursor: String?) async throws -> APINewsFilterNewsResponse {
        var query = [URLQueryItem(name: "filtered", value: String(filtered))]
        if let cursor { query.append(URLQueryItem(name: "cursor", value: cursor)) }
        return try await client.request(APIEndpoints.newsFilterNews, queryItems: query, recoveryPolicy: .safeRead)
    }
}

func newsFilterStatusText(_ status: String) -> String {
    switch status {
    case "matched": "Matches your filter"
    case "filtered_out": "Filtered out of upcoming Briefings"
    case "uncertain": "Uncertain match — allowed in Briefing"
    case "unavailable": "Could not evaluate — kept visible"
    case "published": "Already in your Briefing"
    case "read": "Already read"
    case "pending": "Checking your filter"
    case "outside_window": "Outside the automatic 7-day window"
    case "deferred": "Daily evaluation limit reached"
    case "off": "News filter is off"
    default: "Filter status unavailable"
    }
}
