import Foundation

extension BriefingViewModel {
    /// A stale document remains accepted while its lens is selected or inside
    /// the short return grace period. Expiry only evicts cached presentation;
    /// the normal working-set loader owns every subsequent network request.
    func isLensReplacementProtected(_ key: String) -> Bool {
        lensRetention.isRetained(key)
            || (selectedLensKey == key && !lensRetention.isExpired(key))
    }

    func protectLens(_ key: String) {
        lensRetention.protect(key)
        guard let state = lensStates[key], state.document != nil,
              state.retainsReadRetirement,
              state.isStale || state.loadPhase == .replacingVisible else { return }
        tasks.cancel(.lens(key))
        mutateLensState(key) { $0.loadPhase = .idle }
    }

    func beginLensRetention(for key: String) {
        guard lensStates[key]?.document != nil else { return }
        lensRetention.beginRetaining(key) { [weak self] in
            guard let self, discardStaleLensDocument(key) else { return }
            scheduleSnapshotSave()
        }
    }

    func invalidateLensForIndexChange(_ key: String) {
        let containsOptimisticRead = lensStates[key]?.document?.sources.contains {
            optimisticallyReadSourceKeys.contains($0.sourceKey)
        } == true
        invalidateLens(
            key,
            staleness: containsOptimisticRead ? .readRetirement : .structural
        )
    }

    func invalidateLens(
        _ key: String,
        staleness: BriefingLensState.Staleness = .structural
    ) {
        tasks.cancel(.lens(key))
        if staleness != .readRetirement {
            lensRetention.protect(key)
        }
        mutateLensState(key) { state in
            state.loadPhase = .idle
            state.failure = nil
            state.staleness = staleness
        }
        if staleness == .readRetirement, !isLensReplacementProtected(key) {
            discardStaleLensDocument(key)
        }
    }

    /// An explicit list refresh ends the short reading-retention grace period for segments the
    /// server has already retired. Rehydrate the selected lens immediately from the durable list;
    /// ordinary background reconciliation still preserves the current reading position.
    func reconcileReadRetirementsForManualRefresh() {
        let retiredLensKeys = lensStates.compactMap { key, state in
            state.retainsReadRetirement ? key : nil
        }
        for key in retiredLensKeys {
            discardStaleLensDocument(key)
        }
        if let selectedLensKey, retiredLensKeys.contains(selectedLensKey) {
            loadLensIfNeeded(key: selectedLensKey)
        }
    }

    @discardableResult
    private func discardStaleLensDocument(_ key: String) -> Bool {
        guard var state = lensStates[key], state.isStale, let document = state.document else {
            return false
        }
        reindexSources(for: key, replacing: document, with: nil)
        state.document = nil
        state.renderModel = nil
        state.loadPhase = .idle
        state.failure = nil
        state.staleness = .fresh
        state.documentGeneration += 1
        lensStates[key] = state
        return true
    }
}
