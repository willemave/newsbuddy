//
//  ContentService.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Foundation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ContentService")

struct BulkMarkReadResponse: Codable {
    let status: String
    let markedCount: Int
    let failedIds: [Int]
    let totalRequested: Int

    enum CodingKeys: String, CodingKey {
        case status
        case markedCount = "marked_count"
        case failedIds = "failed_ids"
        case totalRequested = "total_requested"
    }
}

struct ConvertNewsResponse: Codable {
    let newContentId: Int
    let alreadyExists: Bool

    enum CodingKeys: String, CodingKey {
        case newContentId = "new_content_id"
        case alreadyExists = "already_exists"
    }
}

struct DownloadMoreResponse: Codable {
    let status: String
    let requestedCount: Int
    let baseLimit: Int
    let targetLimit: Int
    let scraped: Int
    let saved: Int
    let duplicates: Int
    let errors: Int

    enum CodingKeys: String, CodingKey {
        case status
        case requestedCount = "requested_count"
        case baseLimit = "base_limit"
        case targetLimit = "target_limit"
        case scraped
        case saved
        case duplicates
        case errors
    }
}

struct SubmitContentResponse: Codable {
    let contentId: Int
    let contentType: String
    let status: String
    let platform: String?
    let alreadyExists: Bool
    let message: String
    let taskId: Int?
    let source: String?

    enum CodingKeys: String, CodingKey {
        case contentId = "content_id"
        case contentType = "content_type"
        case status
        case platform
        case alreadyExists = "already_exists"
        case message
        case taskId = "task_id"
        case source
    }
}

struct TrackContentInteractionResponse: Codable {
    let status: String
    let recorded: Bool
    let interactionId: String
    let analyticsInteractionId: Int?

    enum CodingKeys: String, CodingKey {
        case status
        case recorded
        case interactionId = "interaction_id"
        case analyticsInteractionId = "analytics_interaction_id"
    }
}

class ContentService {
    static let shared = ContentService()
    private let client = APIClient.shared
    
    private init() {}
    
    func submitContent(url: URL,
                       contentType: String? = nil,
                       title: String? = nil,
                       platform: String? = nil) async throws -> SubmitContentResponse {
        struct SubmitPayload: Codable {
            let url: String
            let contentType: String?
            let title: String?
            let platform: String?

            enum CodingKeys: String, CodingKey {
                case url
                case contentType = "content_type"
                case title
                case platform
            }
        }

        let payload = SubmitPayload(
            url: url.absoluteString,
            contentType: contentType,
            title: title,
            platform: platform
        )

        let encoder = JSONEncoder()
        let body = try encoder.encode(payload)

        return try await client.request(
            APIEndpoints.submitContent,
            method: "POST",
            body: body
        )
    }
    
    func searchContent(query: String,
                       contentType: String = "all",
                       limit: Int = 25,
                       cursor: String? = nil) async throws -> ContentListResponse {
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "type", value: contentType),
            URLQueryItem(name: "limit", value: String(limit))
        ]

        if let cursor = cursor {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }

        return try await client.request(APIEndpoints.searchContent, queryItems: queryItems)
    }

    func searchMixed(query: String, limit: Int = 10) async throws -> MixedSearchResponse {
        let queryItems: [URLQueryItem] = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "limit", value: String(limit))
        ]
        return try await client.request(APIEndpoints.searchMixedContent, queryItems: queryItems)
    }

    func fetchContentList(contentTypes: [String]? = nil,
                         date: String? = nil,
                         readFilter: String = "all",
                         cursor: String? = nil,
                         limit: Int = 25) async throws -> ContentListResponse {
        var queryItems: [URLQueryItem] = []

        // Support multiple content_type parameters
        if let contentTypes = contentTypes, !contentTypes.isEmpty {
            // Don't filter if contains "all"
            let types = contentTypes.filter { $0 != "all" }
            if !types.isEmpty {
                // Add multiple content_type query parameters
                for type in types {
                    queryItems.append(URLQueryItem(name: "content_type", value: type))
                }
            }
        }

        if let date = date, !date.isEmpty {
            queryItems.append(URLQueryItem(name: "date", value: date))
        }

        queryItems.append(URLQueryItem(name: "read_filter", value: readFilter))
        queryItems.append(URLQueryItem(name: "limit", value: String(limit)))

        if let cursor = cursor {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }

        return try await client.request(APIEndpoints.contentList, queryItems: queryItems)
    }

    func fetchSubmissionStatusList(
        cursor: String? = nil,
        limit: Int = 25
    ) async throws -> SubmissionStatusListResponse {
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: String(limit))
        ]

        if let cursor = cursor {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }

        return try await client.request(APIEndpoints.submissionStatusList, queryItems: queryItems)
    }

    // Backward compatibility: single content type
    func fetchContentList(contentType: String? = nil,
                         date: String? = nil,
                         readFilter: String = "all",
                         cursor: String? = nil,
                         limit: Int = 25) async throws -> ContentListResponse {
        let types = contentType.map { [$0] }
        return try await fetchContentList(contentTypes: types, date: date, readFilter: readFilter, cursor: cursor, limit: limit)
    }
    
    func fetchContentDetail(id: Int) async throws -> ContentDetail {
        let endpoint = APIRequestDescriptor<ContentDetail>(path: APIEndpoints.contentDetail(id: id))
        return try await client.request(endpoint)
    }

    func fetchNewsItemDetail(id: Int) async throws -> ContentDetail {
        let endpoint = APIRequestDescriptor<ContentDetail>(path: APIEndpoints.newsItem(id: id))
        return try await client.request(endpoint)
    }

    func fetchNewsItemList(
        readFilter: String = "all",
        cursor: String? = nil,
        limit: Int = 25
    ) async throws -> ContentListResponse {
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "read_filter", value: readFilter),
            URLQueryItem(name: "limit", value: String(limit))
        ]

        if let cursor {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }

        return try await client.request(APIEndpoints.newsItems, queryItems: queryItems)
    }

    func fetchContentBody(id: Int, variant: String = "source") async throws -> ContentBody {
        let endpoint = APIRequestDescriptor<ContentBody>(
            path: APIEndpoints.contentBody(id: id),
            queryItems: [URLQueryItem(name: "variant", value: variant)]
        )
        return try await client.request(endpoint)
    }

    func refreshContentDiscussion(id: Int, contentType: ContentType? = nil) async throws -> ContentDiscussion {
        let path = if contentType == .news {
            APIEndpoints.newsItemDiscussionRefresh(id: id)
        } else {
            APIEndpoints.contentDiscussionRefresh(id: id)
        }
        let endpoint = APIRequestDescriptor<ContentDiscussion>(
            path: path,
            method: "POST"
        )
        return try await client.request(endpoint)
    }

    func trackContentInteraction(
        contentId: Int,
        interactionType: String,
        interactionId: UUID = UUID(),
        occurredAt: Date = Date(),
        surface: String? = nil,
        contextData: [String: Any] = [:]
    ) async throws -> TrackContentInteractionResponse {
        struct TrackContentInteractionRequest: Codable {
            let interactionId: String
            let contentId: Int
            let interactionType: String
            let occurredAt: String
            let surface: String?
            let contextData: [String: AnyCodable]

            enum CodingKeys: String, CodingKey {
                case interactionId = "interaction_id"
                case contentId = "content_id"
                case interactionType = "interaction_type"
                case occurredAt = "occurred_at"
                case surface
                case contextData = "context_data"
            }
        }

        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]

        let payload = TrackContentInteractionRequest(
            interactionId: interactionId.uuidString.lowercased(),
            contentId: contentId,
            interactionType: interactionType,
            occurredAt: formatter.string(from: occurredAt),
            surface: surface,
            contextData: contextData.mapValues { AnyCodable($0) }
        )
        let body = try JSONEncoder().encode(payload)

        logger.info(
            "[ContentService] trackContentInteraction called | contentId=\(contentId) interactionType=\(interactionType, privacy: .public) interactionId=\(payload.interactionId, privacy: .public)"
        )
        do {
            let response: TrackContentInteractionResponse = try await client.request(
                APIEndpoints.analytics,
                method: "POST",
                body: body
            )
            logger.info(
                "[ContentService] trackContentInteraction success | contentId=\(contentId) interactionType=\(interactionType, privacy: .public) recorded=\(response.recorded)"
            )
            return response
        } catch {
            logger.error(
                "[ContentService] trackContentInteraction failed | contentId=\(contentId) interactionType=\(interactionType, privacy: .public) error=\(error.localizedDescription)"
            )
            throw error
        }
    }

    func trackContentOpened(
        contentId: Int,
        surface: String = "ios_content_detail",
        contextData: [String: Any] = [:]
    ) async throws -> TrackContentInteractionResponse {
        return try await trackContentInteraction(
            contentId: contentId,
            interactionType: "opened",
            surface: surface,
            contextData: contextData
        )
    }

    func downloadMoreFromSeries(contentId: Int, count: Int) async throws -> DownloadMoreResponse {
        struct DownloadMoreRequest: Codable {
            let count: Int
        }

        let body = try JSONEncoder().encode(DownloadMoreRequest(count: count))
        return try await client.request(
            APIEndpoints.downloadMoreFromSeries(id: contentId),
            method: "POST",
            body: body
        )
    }
    
    func markContentAsRead(id: Int, contentType: ContentType? = nil) async throws {
        logger.info(
            "[ContentService] markContentAsRead called | id=\(id) contentType=\(contentType?.rawValue ?? "nil", privacy: .public)"
        )
        do {
            if contentType == .news {
                _ = try await bulkMarkNewsItemsAsRead(newsItemIds: [id])
            } else {
                try await client.requestVoid(APIEndpoints.markContentRead(id: id), method: "POST")
            }
            logger.info("[ContentService] markContentAsRead success | id=\(id)")
        } catch {
            logger.error(
                "[ContentService] markContentAsRead failed | id=\(id) contentType=\(contentType?.rawValue ?? "nil", privacy: .public) error=\(error.localizedDescription)"
            )
            throw error
        }
    }
    
    func markContentAsUnread(id: Int) async throws {
        try await client.requestVoid(APIEndpoints.markContentUnread(id: id), method: "DELETE")
    }
    
    func bulkMarkAsRead(contentIds: [Int]) async throws -> BulkMarkReadResponse {
        logger.info("[ContentService] bulkMarkAsRead called | ids=\(contentIds, privacy: .public) count=\(contentIds.count)")

        struct BulkMarkReadRequest: Codable {
            let contentIds: [Int]

            enum CodingKeys: String, CodingKey {
                case contentIds = "content_ids"
            }
        }

        let request = BulkMarkReadRequest(contentIds: contentIds)
        let encoder = JSONEncoder()
        let body = try encoder.encode(request)

        do {
            let response: BulkMarkReadResponse = try await client.request(
                APIEndpoints.bulkMarkRead,
                method: "POST",
                body: body
            )
            logger.info("[ContentService] bulkMarkAsRead success | markedCount=\(response.markedCount) failedIds=\(response.failedIds, privacy: .public)")
            return response
        } catch {
            logger.error("[ContentService] bulkMarkAsRead failed | ids=\(contentIds, privacy: .public) error=\(error.localizedDescription)")
            throw error
        }
    }

    func bulkMarkNewsItemsAsRead(newsItemIds: [Int]) async throws -> BulkMarkReadResponse {
        logger.info("[ContentService] bulkMarkNewsItemsAsRead called | ids=\(newsItemIds, privacy: .public) count=\(newsItemIds.count)")

        struct BulkMarkReadRequest: Codable {
            let contentIds: [Int]

            enum CodingKeys: String, CodingKey {
                case contentIds = "content_ids"
            }
        }

        let request = BulkMarkReadRequest(contentIds: newsItemIds)
        let encoder = JSONEncoder()
        let body = try encoder.encode(request)

        do {
            let response: BulkMarkReadResponse = try await client.request(
                APIEndpoints.newsItemsMarkRead,
                method: "POST",
                body: body
            )
            logger.info("[ContentService] bulkMarkNewsItemsAsRead success | markedCount=\(response.markedCount) failedIds=\(response.failedIds, privacy: .public)")
            return response
        } catch {
            logger.error("[ContentService] bulkMarkNewsItemsAsRead failed | ids=\(newsItemIds, privacy: .public) error=\(error.localizedDescription)")
            throw error
        }
    }

    func markAllAsRead(contentType: String) async throws -> BulkMarkReadResponse? {
        var allUnreadIds: [Int] = []
        var cursor: String? = nil

        // Loop through all pages until hasMore is false
        repeat {
            let response: ContentListResponse
            if contentType == APIContentType.news.rawValue {
                response = try await fetchNewsItemList(
                    readFilter: "unread",
                    cursor: cursor,
                    limit: 100
                )
            } else {
                response = try await fetchContentList(
                    contentType: contentType,
                    readFilter: "unread",
                    cursor: cursor,
                    limit: 100  // Fetch larger batches for efficiency
                )
            }

            // Collect unread IDs from this page
            let pageUnreadIds = response.contents
                .filter { !$0.isRead }
                .map { $0.id }

            allUnreadIds.append(contentsOf: pageUnreadIds)

            // Update cursor for next iteration
            cursor = response.nextCursor

            // Continue if there are more pages
            if !response.hasMore {
                break
            }
        } while cursor != nil

        guard !allUnreadIds.isEmpty else {
            return nil
        }

        if contentType == APIContentType.news.rawValue {
            return try await bulkMarkNewsItemsAsRead(newsItemIds: allUnreadIds)
        }

        return try await bulkMarkAsRead(contentIds: allUnreadIds)
    }
    
    func saveToKnowledge(id: Int) async throws -> [String: Any] {
        return try await client.requestRaw(APIEndpoints.saveToKnowledge(id: id), method: "POST")
    }

    func removeFromKnowledge(id: Int) async throws {
        try await client.requestVoid(APIEndpoints.removeFromKnowledge(id: id), method: "DELETE")
    }

    func fetchKnowledgeLibrary(cursor: String? = nil, limit: Int = 25) async throws -> ContentListResponse {
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: String(limit))
        ]

        if let cursor = cursor {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }

        return try await client.request(APIEndpoints.knowledgeLibraryList, queryItems: queryItems)
    }

    func fetchRecentlyReadList(cursor: String? = nil, limit: Int = 25) async throws -> ContentListResponse {
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: String(limit))
        ]

        if let cursor = cursor {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }

        return try await client.request(APIEndpoints.recentlyReadList, queryItems: queryItems)
    }

    func getChatGPTUrl(id: Int) async throws -> String {
        struct ChatGPTUrlResponse: Codable {
            let chatUrl: String
            let truncated: Bool

            enum CodingKeys: String, CodingKey {
                case chatUrl = "chat_url"
                case truncated
            }
        }

        let response: ChatGPTUrlResponse = try await client.request(APIEndpoints.chatGPTUrl(id: id))
        return response.chatUrl
    }

    func convertNewsToArticle(id: Int) async throws -> ConvertNewsResponse {
        return try await client.request(
            APIEndpoints.convertNewsToArticle(id: id),
            method: "POST"
        )
    }

    func convertNewsItemToArticle(id: Int) async throws -> ConvertNewsResponse {
        return try await client.request(
            APIEndpoints.convertNewsItemToArticle(id: id),
            method: "POST"
        )
    }

    func generateTweetSuggestions(
        id: Int,
        message: String? = nil,
        creativity: Int = 5,
        provider: ChatModelProvider? = nil
    ) async throws -> TweetSuggestionsResponse {
        let request = TweetSuggestionsRequest(
            message: message,
            creativity: creativity,
            llmProvider: provider?.rawValue
        )
        let encoder = JSONEncoder()
        let body = try encoder.encode(request)

        return try await client.request(
            APIEndpoints.tweetSuggestions(id: id),
            method: "POST",
            body: body
        )
    }
}
