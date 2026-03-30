//
//  PodcastDetailViewModel.swift
//  newsly
//
//  Created by Assistant on 8/9/25.
//

import Foundation
import Combine

@MainActor
class PodcastDetailViewModel: ObservableObject {
    @Published var podcast: ContentDetail?
    @Published var contentBody: ContentBody?
    @Published var podcastMetadata: PodcastMetadata?
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var isTranscriptExpanded = false
    
    private let contentService = ContentService.shared
    private var cancellables = Set<AnyCancellable>()
    
    func loadPodcast(id: Int) async {
        isLoading = true
        errorMessage = nil
        
        do {
            let content = try await contentService.fetchContentDetail(id: id)
            if content.apiContentType == .podcast {
                self.podcast = content
                self.podcastMetadata = content.podcastMetadata
                if content.bodyAvailable {
                    self.contentBody = try? await contentService.fetchContentBody(id: id)
                }
            } else {
                errorMessage = "Content is not a podcast"
            }
        } catch {
            errorMessage = error.localizedDescription
        }
        
        isLoading = false
    }
    
    func markAsPlayed() async {
        guard let podcast = podcast else { return }
        
        do {
            try await contentService.markContentAsRead(id: podcast.id)
            // Refresh podcast to get updated state
            await loadPodcast(id: podcast.id)
        } catch {
            errorMessage = "Failed to mark as played: \(error.localizedDescription)"
        }
    }
    
    func markAsUnplayed() async {
        guard let podcast = podcast else { return }
        
        do {
            try await contentService.markContentAsUnread(id: podcast.id)
            // Refresh podcast to get updated state
            await loadPodcast(id: podcast.id)
        } catch {
            errorMessage = "Failed to mark as unplayed: \(error.localizedDescription)"
        }
    }
    
    var displayTitle: String {
        podcast?.displayTitle ?? podcastMetadata?.summary?.title ?? "Untitled Podcast"
    }
    
    var episodeNumber: String? {
        guard let number = podcastMetadata?.episodeNumber else { return nil }
        return "Episode #\(number)"
    }
    
    var duration: String? {
        podcastMetadata?.formattedDuration
    }
    
    var source: String? {
        podcastMetadata?.source ?? podcast?.source
    }
    
    var channelName: String? {
        podcastMetadata?.channelName
    }
    
    var viewCount: String? {
        podcastMetadata?.formattedViewCount
    }
    
    var likeCount: String? {
        guard let count = podcastMetadata?.likeCount else { return nil }
        
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.maximumFractionDigits = 1
        
        if count >= 1_000_000 {
            let millions = Double(count) / 1_000_000
            return "\(formatter.string(from: NSNumber(value: millions)) ?? "0")M likes"
        } else if count >= 1_000 {
            let thousands = Double(count) / 1_000
            return "\(formatter.string(from: NSNumber(value: thousands)) ?? "0")K likes"
        } else {
            return "\(count) likes"
        }
    }
    
    var audioUrl: String? {
        podcastMetadata?.audioUrl
    }
    
    var videoUrl: String? {
        podcastMetadata?.videoUrl
    }
    
    var thumbnailUrl: String? {
        podcastMetadata?.thumbnailUrl
    }
    
    var transcript: String? {
        contentBody?.text ?? podcastMetadata?.transcript
    }
    
    var hasTranscript: Bool {
        podcastMetadata?.hasTranscript ?? (transcript != nil)
    }
    
    var structuredSummary: StructuredSummary? {
        podcastMetadata?.summary ?? podcast?.structuredSummary
    }
    
    var isYouTube: Bool {
        podcastMetadata?.videoUrl != nil || podcastMetadata?.videoId != nil
    }
    
    func toggleTranscript() {
        isTranscriptExpanded.toggle()
    }
}
