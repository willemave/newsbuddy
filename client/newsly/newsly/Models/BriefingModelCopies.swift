import Foundation

extension APIBriefingIndexResponse {
    func with(
        version: Int? = nil,
        lenses: [APIBriefingLensSummary]? = nil
    ) -> APIBriefingIndexResponse {
        APIBriefingIndexResponse(
            version: version ?? self.version,
            mastheadTitle: mastheadTitle,
            mastheadDeck: mastheadDeck,
            generatedAt: generatedAt,
            lenses: lenses ?? self.lenses,
            firstRun: firstRun
        )
    }

    func withoutFirstRun() -> APIBriefingIndexResponse {
        APIBriefingIndexResponse(
            version: version,
            mastheadTitle: mastheadTitle,
            mastheadDeck: mastheadDeck,
            generatedAt: generatedAt,
            lenses: lenses,
            firstRun: nil
        )
    }
}

extension APIBriefingLensSummary {
    func with(unreadSourceCount: Int) -> APIBriefingLensSummary {
        APIBriefingLensSummary(
            key: key,
            tier: tier,
            title: title,
            deck: deck,
            position: position,
            segmentCount: segmentCount,
            unreadSourceCount: unreadSourceCount
        )
    }
}

extension APIBriefingLensResponse {
    func with(
        version: Int? = nil,
        lens: APIBriefingLensSummary? = nil,
        sources: [APIBriefingSource]? = nil
    ) -> APIBriefingLensResponse {
        APIBriefingLensResponse(
            version: version ?? self.version,
            lens: lens ?? self.lens,
            segments: segments,
            sources: sources ?? self.sources,
            nextCursor: nextCursor,
            hasMore: hasMore
        )
    }
}

extension APIBriefingSource {
    func markedRead() -> APIBriefingSource {
        guard !read else { return self }
        return APIBriefingSource(
            sourceKey: sourceKey,
            kind: kind,
            id: id,
            title: title,
            publisher: publisher,
            aggregator: aggregator,
            summary: summary,
            keyPoints: keyPoints,
            url: url,
            imageUrl: imageUrl,
            thumbnailUrl: thumbnailUrl,
            publishedAt: publishedAt,
            contentType: contentType,
            durationSeconds: durationSeconds,
            readingMinutes: readingMinutes,
            read: true,
            discussion: discussion
        )
    }
}
