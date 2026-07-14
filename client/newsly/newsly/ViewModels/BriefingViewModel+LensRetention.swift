import Foundation

extension BriefingViewModel {
    /// A stale document remains accepted while its lens is selected or inside
    /// the short return grace period. Expiry only evicts cached presentation;
    /// the normal working-set loader owns every subsequent network request.
    func isLensReplacementProtected(_ key: String) -> Bool {
        (isActive && selectedLensKey == key) || lensRetention.contains(key)
    }

    func protectLens(_ key: String) {
        lensRetention.protect(key)
        guard let state = lensStates[key], state.document != nil,
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

    func invalidateLens(_ key: String) {
        tasks.cancel(.lens(key))
        mutateLensState(key) { state in
            state.loadPhase = .idle
            state.failure = nil
            state.isStale = true
        }
        if !isLensReplacementProtected(key) {
            discardStaleLensDocument(key)
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
        state.isStale = false
        lensStates[key] = state
        return true
    }
}
