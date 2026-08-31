//
//  ContentService.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Foundation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ContentService")

typealias BulkMarkReadResponse = APIBulkMarkReadResponse
typealias KnowledgeMutationResponse = APIKnowledgeMutationResponse
typealias SubmitContentResponse = APIContentSubmissionResponse
typealias TrackContentInteractionResponse = APIRecordContentInteractionResponse
typealias DownloadMoreResponse = APIDownloadMoreResponse

struct ConvertNewsResponse {
    let newContentId: Int
    let alreadyExists: Bool

    init(api response: APIConvertNewsResponse) {
        newContentId = response.newContentId
        alreadyExists = response.alreadyExists
    }

    init(api response: APIConvertNewsItemResponse) {
        newContentId = response.newContentId
        alreadyExists = response.alreadyExists
    }
}

enum ContentServiceError: LocalizedError {
    case unsupportedInteractionType(String)

    var errorDescription: String? {
        switch self {
        case .unsupportedInteractionType(let value):
            "Unsupported content interaction type: \(value)"
        }
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
        let payload = APISubmitContentRequest(
            url: url.absoluteString,
            contentType: contentType.map(APIContentType.init(rawValue:)),
            title: title,
            platform: platform
        )

        let encoder = JSONEncoder()
        let body = try encoder.encode(payload)

        return try await client.request(
            APIEndpoints.submitContent,
            method: .post,
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

        let response: APIContentListResponse = try await client.request(
            APIEndpoints.searchContent,
            queryItems: queryItems
        )
        return ContentListResponse(api: response)
    }

    func searchMixed(query: String, limit: Int = 10) async throws -> MixedSearchResponse {
        let queryItems: [URLQueryItem] = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "limit", value: String(limit))
        ]
        let response: APIMixedSearchResponse = try await client.request(
            APIEndpoints.searchMixedContent,
            queryItems: queryItems
        )
        return MixedSearchResponse(api: response)
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

        let response: APIContentListResponse = try await client.request(
            APIEndpoints.contentList,
            queryItems: queryItems,
            recoveryPolicy: .safeRead
        )
        return ContentListResponse(api: response)
    }

    func fetchSubmissionStatusList(
        cursor: String? = nil,
        limit: Int = 25
    ) async throws -> SubmissionStatusFeed {
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: String(limit))
        ]

        if let cursor = cursor {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }

        let response: APISubmissionStatusListResponse = try await client.request(
            APIEndpoints.submissionStatusList,
            queryItems: queryItems
        )
        return SubmissionStatusFeed(api: response)
    }

    func fetchContentDetail(id: Int) async throws -> ContentDetail {
        let response: APIContentDetailResponse = try await client.request(
            APIEndpoints.contentDetail(id: id),
            recoveryPolicy: .safeRead
        )
        return try ContentDetail(api: response)
    }

    func fetchNewsItemDetail(id: Int) async throws -> ContentDetail {
        let response: APINewsItemDetailResponse = try await client.request(
            APIEndpoints.newsItem(id: id),
            recoveryPolicy: .safeRead
        )
        return ContentDetail(api: response)
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

        let response: APINewsItemListResponse = try await client.request(
            APIEndpoints.newsItems,
            queryItems: queryItems,
            recoveryPolicy: .safeRead
        )
        return ContentListResponse(api: response)
    }

    func fetchContentBody(
        id: Int,
        variant: String = "source",
        contentType: APIContentType? = nil
    ) async throws -> ContentBody {
        let path = if contentType == .news {
            APIEndpoints.newsItemBody(id: id)
        } else {
            APIEndpoints.contentBody(id: id)
        }
        let response: APIContentBodyResponse = try await client.request(
            path,
            queryItems: [URLQueryItem(name: "variant", value: variant)],
            recoveryPolicy: .safeRead
        )
        return ContentBody(api: response)
    }

    func fetchContentDiscussion(id: Int, contentType: APIContentType? = nil) async throws -> ContentDiscussion {
        let path = if contentType == .news {
            APIEndpoints.newsItemDiscussion(id: id)
        } else {
            APIEndpoints.contentDiscussion(id: id)
        }
        let response: APIContentDiscussionResponse = try await client.request(path)
        return ContentDiscussion(api: response)
    }

    func trackContentInteraction(
        contentId: Int,
        interactionType: String,
        interactionId: UUID = UUID(),
        occurredAt: Date = Date(),
        surface: String? = nil,
        contextData: [String: Any] = [:]
    ) async throws -> TrackContentInteractionResponse {
        guard let typedInteraction = APIContentInteractionType(rawValue: interactionType) else {
            throw ContentServiceError.unsupportedInteractionType(interactionType)
        }
        let payload = APIRecordContentInteractionRequest(
            interactionId: interactionId.uuidString.lowercased(),
            contentId: contentId,
            interactionType: typedInteraction,
            occurredAt: occurredAt,
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
                method: .post,
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
        let body = try JSONEncoder().encode(APIDownloadMoreRequest(count: count))
        return try await client.request(
            APIEndpoints.downloadMoreFromSeries(id: contentId),
            method: .post,
            body: body
        )
    }
    
    func markContentAsRead(id: Int, contentType: APIContentType? = nil) async throws {
        logger.info(
            "[ContentService] markContentAsRead called | id=\(id) contentType=\(contentType?.rawValue ?? "nil", privacy: .public)"
        )
        do {
            if contentType == .news {
                _ = try await bulkMarkNewsItemsAsRead(newsItemIds: [id])
            } else {
                let _: APIMarkReadResponse = try await client.request(
                    APIEndpoints.markContentRead(id: id),
                    method: .post
                )
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
        let _: APIMarkUnreadResponse = try await client.request(
            APIEndpoints.markContentUnread(id: id),
            method: .delete
        )
    }
    
    func bulkMarkAsRead(contentIds: [Int]) async throws -> BulkMarkReadResponse {
        logger.info("[ContentService] bulkMarkAsRead called | ids=\(contentIds, privacy: .public) count=\(contentIds.count)")

        let request = APIBulkMarkReadRequest(contentIds: contentIds)
        let encoder = JSONEncoder()
        let body = try encoder.encode(request)

        do {
            let response: BulkMarkReadResponse = try await client.request(
                APIEndpoints.bulkMarkRead,
                method: .post,
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

        let request = APIBulkMarkReadRequest(contentIds: newsItemIds)
        let encoder = JSONEncoder()
        let body = try encoder.encode(request)

        do {
            let response: BulkMarkReadResponse = try await client.request(
                APIEndpoints.newsItemsMarkRead,
                method: .post,
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
                    contentTypes: [contentType],
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
    
    func saveToKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        try await client.request(APIEndpoints.saveToKnowledge(id: id), method: .post)
    }

    @discardableResult
    func removeFromKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        try await client.request(APIEndpoints.removeFromKnowledge(id: id), method: .delete)
    }

    func fetchKnowledgeLibrary(
        query: String? = nil,
        cursor: String? = nil,
        limit: Int = 25
    ) async throws -> ContentListResponse {
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: String(limit))
        ]

        if let query, !query.isEmpty {
            queryItems.append(URLQueryItem(name: "q", value: query))
        }

        if let cursor = cursor {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }

        let response: APIContentListResponse = try await client.request(
            APIEndpoints.knowledgeLibraryList,
            queryItems: queryItems,
            recoveryPolicy: .safeRead
        )
        return ContentListResponse(api: response)
    }

    func fetchRecentlyReadList(
        contentType: String? = nil,
        date: String? = nil,
        cursor: String? = nil,
        limit: Int = 25
    ) async throws -> ContentListResponse {
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "limit", value: String(limit))
        ]

        if let contentType, contentType != "all" {
            queryItems.append(URLQueryItem(name: "content_type", value: contentType))
        }

        if let date, !date.isEmpty {
            queryItems.append(URLQueryItem(name: "date", value: date))
        }

        if let cursor = cursor {
            queryItems.append(URLQueryItem(name: "cursor", value: cursor))
        }

        let response: APIContentListResponse = try await client.request(
            APIEndpoints.recentlyReadList,
            queryItems: queryItems,
            recoveryPolicy: .safeRead
        )
        return ContentListResponse(api: response)
    }

    func convertNewsToArticle(id: Int) async throws -> ConvertNewsResponse {
        let response: APIConvertNewsResponse = try await client.request(
            APIEndpoints.convertNewsToArticle(id: id),
            method: .post
        )
        return ConvertNewsResponse(api: response)
    }

    func convertNewsItemToArticle(id: Int) async throws -> ConvertNewsResponse {
        let response: APIConvertNewsItemResponse = try await client.request(
            APIEndpoints.convertNewsItemToArticle(id: id),
            method: .post
        )
        return ConvertNewsResponse(api: response)
    }

    func generateTweetSuggestions(
        id: Int,
        message: String? = nil,
        creativity: Int = 5,
        provider: ChatModelProvider? = nil
    ) async throws -> TweetSuggestionsResponse {
        let request = APITweetSuggestionsRequest(
            message: message,
            creativity: creativity,
            length: .medium,
            llmProvider: provider.flatMap { APIUserLlmProvider(rawValue: $0.rawValue) }
        )
        let encoder = JSONEncoder()
        let body = try encoder.encode(request)

        let response: APITweetSuggestionsResponse = try await client.request(
            APIEndpoints.tweetSuggestions(id: id),
            method: .post,
            body: body
        )
        return TweetSuggestionsResponse(api: response)
    }
}
