//
//  ArticleDetailViewModel.swift
//  newsly
//
//  Created by Assistant on 8/9/25.
//

import Foundation
import Combine

@MainActor
class ArticleDetailViewModel: ObservableObject {
    @Published var article: ContentDetail?
    @Published var contentBody: ContentBody?
    @Published var articleMetadata: ArticleMetadata?
    @Published var isLoading = false
    @Published var errorMessage: String?
    
    private let contentService = ContentService.shared
    private var cancellables = Set<AnyCancellable>()
    
    func loadArticle(id: Int) async {
        isLoading = true
        errorMessage = nil
        
        do {
            let content = try await contentService.fetchContentDetail(id: id)
            if content.apiContentType == .article {
                self.article = content
                self.articleMetadata = content.articleMetadata
                if content.bodyAvailable {
                    self.contentBody = try? await contentService.fetchContentBody(id: id)
                }
            } else {
                errorMessage = "Content is not an article"
            }
        } catch {
            errorMessage = error.localizedDescription
        }
        
        isLoading = false
    }
    
    func markAsRead() async {
        guard let article = article else { return }
        
        do {
            try await contentService.markContentAsRead(id: article.id)
            // Refresh article to get updated state
            await loadArticle(id: article.id)
        } catch {
            errorMessage = "Failed to mark as read: \(error.localizedDescription)"
        }
    }
    
    func markAsUnread() async {
        guard let article = article else { return }
        
        do {
            try await contentService.markContentAsUnread(id: article.id)
            // Refresh article to get updated state
            await loadArticle(id: article.id)
        } catch {
            errorMessage = "Failed to mark as unread: \(error.localizedDescription)"
        }
    }
    
    var displayTitle: String {
        article?.displayTitle ?? articleMetadata?.summary?.title ?? "Untitled Article"
    }
    
    var author: String? {
        articleMetadata?.author
    }
    
    var publicationDate: String? {
        guard let date = articleMetadata?.publicationDate else { return nil }
        
        let formatter = DateFormatter()
        formatter.dateStyle = .long
        formatter.timeStyle = .none
        return formatter.string(from: date)
    }
    
    var wordCount: String? {
        guard let count = articleMetadata?.wordCount else { return nil }
        return "\(count) words"
    }
    
    var readingTime: String? {
        guard let count = articleMetadata?.wordCount else { return nil }
        let minutes = max(1, count / 250) // Assuming 250 words per minute
        return "\(minutes) min read"
    }
    
    var source: String? {
        articleMetadata?.source ?? article?.source
    }
    
    var contentMarkdown: String? {
        contentBody?.text ?? articleMetadata?.fullMarkdown ?? articleMetadata?.content
    }
    
    var structuredSummary: StructuredSummary? {
        articleMetadata?.summary ?? article?.structuredSummary
    }
}
