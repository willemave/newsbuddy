import Foundation
import OSLog

private let briefingLensPageLimit = 12

extension BriefingViewModel {
    var lenses: [String: APIBriefingLensResponse] {
        lensStates.compactMapValues(\.document)
    }

    var lensErrors: [String: String] {
        lensStates.compactMapValues(\.initialError)
    }

    var lensContinuationErrors: [String: String] {
        lensStates.compactMapValues(\.continuationError)
    }

    var lensContinuationLoadingKeys: Set<String> {
        Set(lensStates.compactMap { key, state in
            state.isLoadingContinuation ? key : nil
        })
    }

    func documentGeneration(for lensKey: String) -> Int {
        lensStates[lensKey]?.documentGeneration ?? 0
    }

    func renderModel(for lensKey: String) -> BriefingLensRenderModel? {
        lensStates[lensKey]?.renderModel
    }

    func materializeRenderModelIfNeeded(for lensKey: String) {
        guard var state = lensStates[lensKey],
              state.renderModel == nil,
              let lens = state.document else {
            return
        }
        state.renderModel = makeRenderModel(lens, for: lensKey)
        lensStates[lensKey] = state
    }

    func loadLensIfNeeded(key: String) {
        let currentState = lensStates[key] ?? BriefingLensState()
        guard key == selectedLensKey || !lensRetention.isExpired(key) else {
            return
        }
        let existing = currentState.document
        let requiresReplacement = currentState.isStale
            || existing.map { $0.version != index?.version } == true
        let requiresContinuation = existing?.hasMore == true
        guard (existing == nil || requiresReplacement || requiresContinuation),
              !tasks.isRunning(.lens(key)) else {
            return
        }
        let keepsRetiredDocumentVisible = existing != nil
            && requiresReplacement
            && currentState.retainsReadRetirement
            && isLensReplacementProtected(key)
        guard !keepsRetiredDocumentVisible else {
            return
        }
        mutateLensState(key) { state in
            state.failure = nil
            if requiresReplacement, existing != nil {
                state.loadPhase = .replacingVisible
            } else if requiresContinuation {
                state.loadPhase = .continuation
            } else {
                state.loadPhase = .initial
            }
        }
        tasks.runReplacing(.lens(key)) { [weak self] token in
            guard let self else { return }
            let signpostState = BriefingPerformance.signposter.beginInterval("lens-hydration")
            defer { BriefingPerformance.signposter.endInterval("lens-hydration", signpostState) }
            let startedAt = Date()
            let visibleExisting = self.lensStates[key]?.document
            let replacingVisibleLens = requiresReplacement && visibleExisting != nil
            var assembled = replacingVisibleLens ? nil : visibleExisting
            var cursor = requiresReplacement ? nil : assembled?.nextCursor
            var pageCount = 0
            do {
                while true {
                    let response = try await service.fetchLens(
                        key: key,
                        limit: briefingLensPageLimit,
                        cursor: cursor
                    )
                    guard self.tasks.isCurrent(token), !Task.isCancelled else {
                        return
                    }
                    guard let normalized = self.normalizedLensPage(response, for: key) else {
                        self.mutateLensState(key) { $0.loadPhase = .idle }
                        if !Task.isCancelled {
                            await self.refreshIndex()
                        }
                        return
                    }
                    assembled = self.mergingLensPage(assembled, with: normalized)
                    pageCount += 1

                    if !replacingVisibleLens, let assembled {
                        self.installLens(
                            assembled,
                            for: key,
                            reusingUnchangedPrefix: visibleExisting != nil || pageCount > 1
                        ) { state in
                            state.staleness = .fresh
                            state.failure = nil
                            state.loadPhase = assembled.hasMore ? .continuation : .idle
                        }
                        self.scheduleSnapshotSave()
                    }

                    guard normalized.hasMore else { break }
                    guard let nextCursor = normalized.nextCursor,
                          nextCursor != cursor else {
                        throw PagingError.missingCursor
                    }
                    cursor = nextCursor
                }

                guard let assembled else { return }
                if replacingVisibleLens {
                    self.installLens(assembled, for: key) { state in
                        state.staleness = .fresh
                        state.failure = nil
                        state.loadPhase = .idle
                    }
                }
                BriefingPerformance.logger.info(
                    "Lens loaded | key=\(key, privacy: .public) duration_ms=\(Int(Date().timeIntervalSince(startedAt) * 1_000), privacy: .public) version=\(assembled.version, privacy: .public) pages=\(pageCount, privacy: .public) segments=\(assembled.segments.count, privacy: .public) foreground=\(key == self.selectedLensKey, privacy: .public)"
                )
                self.scheduleSnapshotSave()
                self.prefetchNeighborsIfSelected(key)
            } catch {
                guard self.tasks.isCurrent(token), !Task.isCancelled else {
                    return
                }
                guard !isNetworkCancellation(error) else {
                    self.mutateLensState(key) { $0.loadPhase = .idle }
                    return
                }
                if let fetchError = error as? BriefingLensFetchError,
                   case .staleCursor = fetchError {
                    self.mutateLensState(key) { state in
                        state.loadPhase = .idle
                        state.staleness = .structural
                        state.failure = .continuation(error.localizedDescription)
                    }
                    return
                }
                if replacingVisibleLens || self.lenses[key]?.hasMore == true {
                    self.mutateLensState(key) { state in
                        state.loadPhase = .idle
                        state.failure = .continuation(error.localizedDescription)
                    }
                } else if key == self.selectedLensKey, self.lenses[key] == nil {
                    self.mutateLensState(key) { state in
                        state.loadPhase = .idle
                        state.failure = .initial(error.localizedDescription)
                    }
                } else {
                    self.mutateLensState(key) { $0.loadPhase = .idle }
                }
            }
        }
    }

    func retryLens(key: String) {
        tasks.cancel(.lens(key))
        loadLensIfNeeded(key: key)
    }

    func mutateLensState(
        _ key: String,
        _ mutation: (inout BriefingLensState) -> Void
    ) {
        var state = lensStates[key] ?? BriefingLensState()
        mutation(&state)
        lensStates[key] = state
    }

    private func normalizedLensPage(
        _ response: APIBriefingLensResponse,
        for lensKey: String
    ) -> APIBriefingLensResponse? {
        guard let currentVersion = index?.version else { return nil }
        let usesReadCompatibility: Bool
        if response.version == currentVersion {
            usesReadCompatibility = false
        } else if let compatibility = readVersionCompatibility,
                  response.version >= compatibility.oldestCompatibleVersion,
                  response.version < compatibility.currentVersion,
                  currentVersion == compatibility.currentVersion {
            usesReadCompatibility = true
        } else {
            return nil
        }

        let priorSources = lenses[lensKey]?.sources ?? []
        let knownSources = priorSources + response.sources
        let optimisticUnreadCount = Dictionary(
            knownSources.map { ($0.sourceKey, $0) },
            uniquingKeysWith: { _, current in current }
        ).values.count(where: { source in
            optimisticallyReadSourceKeys.contains(source.sourceKey) && !source.read
        })
        let summary = if usesReadCompatibility, let existingSummary = lenses[lensKey]?.lens {
            existingSummary
        } else {
            APIBriefingLensSummary(
                key: response.lens.key,
                tier: response.lens.tier,
                title: response.lens.title,
                deck: response.lens.deck,
                position: response.lens.position,
                segmentCount: response.lens.segmentCount,
                unreadSourceCount: max(
                    response.lens.unreadSourceCount - optimisticUnreadCount,
                    0
                )
            )
        }
        let normalizedSources = response.sources.map(applyingOptimisticReadOverlay)
        optimisticallyReadSourceKeys.subtract(
            response.sources.lazy.filter(\.read).map(\.sourceKey)
        )
        return APIBriefingLensResponse(
            version: currentVersion,
            lens: summary,
            segments: response.segments,
            sources: normalizedSources,
            nextCursor: response.nextCursor,
            hasMore: response.hasMore
        )
    }

    private func mergingLensPage(
        _ existing: APIBriefingLensResponse?,
        with page: APIBriefingLensResponse
    ) -> APIBriefingLensResponse {
        var segmentIDs = Set(existing?.segments.map(\.id) ?? [])
        var segments = existing?.segments ?? []
        for segment in page.segments where segmentIDs.insert(segment.id).inserted {
            segments.append(segment)
        }

        var sourceOrder = existing?.sources.map(\.sourceKey) ?? []
        var sourcesByKey = Dictionary(
            (existing?.sources ?? []).map { ($0.sourceKey, $0) },
            uniquingKeysWith: { _, current in current }
        )
        for source in page.sources {
            if sourcesByKey[source.sourceKey] == nil {
                sourceOrder.append(source.sourceKey)
            }
            sourcesByKey[source.sourceKey] = source
        }
        return APIBriefingLensResponse(
            version: page.version,
            lens: page.lens,
            segments: segments,
            sources: sourceOrder
                .compactMap { sourcesByKey[$0] }
                .map(applyingOptimisticReadOverlay),
            nextCursor: page.nextCursor,
            hasMore: page.hasMore
        )
    }

    private func installLens(
        _ lens: APIBriefingLensResponse,
        for key: String,
        reusingUnchangedPrefix: Bool = false,
        stateUpdate: (inout BriefingLensState) -> Void = { _ in }
    ) {
        var state = lensStates[key] ?? BriefingLensState()
        let previousLens = state.document
        let previousRenderModel = state.renderModel
        let previousIDs = previousLens?.segments.map(\.id) ?? []
        let mergedIDs = lens.segments.map(\.id)
        if !BriefingLensDocumentGenerationPolicy.preservesGeneration(
            previousIDs: previousIDs,
            mergedIDs: mergedIDs
        ) {
            state.documentGeneration += 1
        }
        reindexSources(for: key, replacing: previousLens, with: lens)
        state.document = lens
        if key == selectedLensKey {
            state.renderModel = makeRenderModel(
                lens,
                for: key,
                reusing: reusingUnchangedPrefix ? previousRenderModel : nil,
                affectedSourceKeys: reusingUnchangedPrefix ? Set<String>() : nil
            )
        } else {
            state.renderModel = nil
        }
        stateUpdate(&state)
        lensStates[key] = state
        BriefingPerformance.signposter.emitEvent("lens-published")
    }

    func makeRenderModel(
        _ lens: APIBriefingLensResponse,
        for key: String,
        reusing previousRenderModel: BriefingLensRenderModel? = nil,
        affectedSourceKeys: Set<String>? = nil
    ) -> BriefingLensRenderModel {
        let renderStartedAt = Date()
        let signpostState = BriefingPerformance.signposter.beginInterval("render-model")
        let renderModel = BriefingLensRenderModel(
            lens: lens,
            reusing: previousRenderModel,
            affectedSourceKeys: affectedSourceKeys
        )
        BriefingPerformance.signposter.endInterval("render-model", signpostState)
        BriefingPerformance.logger.info(
            "Lens render model built | key=\(key, privacy: .public) version=\(lens.version, privacy: .public) segments=\(lens.segments.count, privacy: .public) sources=\(lens.sources.count, privacy: .public) duration_ms=\(Int(Date().timeIntervalSince(renderStartedAt) * 1_000), privacy: .public)"
        )
        return renderModel
    }

    func applyingOptimisticReadOverlay(
        _ source: APIBriefingSource
    ) -> APIBriefingSource {
        guard optimisticallyReadSourceKeys.contains(source.sourceKey), !source.read else {
            return source
        }
        return APIBriefingSource(
            sourceKey: source.sourceKey,
            kind: source.kind,
            id: source.id,
            title: source.title,
            summary: source.summary,
            keyPoints: source.keyPoints,
            url: source.url,
            imageUrl: source.imageUrl,
            thumbnailUrl: source.thumbnailUrl,
            publishedAt: source.publishedAt,
            contentType: source.contentType,
            read: true,
            discussion: source.discussion
        )
    }
}
