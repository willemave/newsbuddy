import Foundation

/// All mutable client state for one Briefing Lens. Keeping the document,
/// presentation model, paging phase, and failure together makes each published
/// transition internally consistent.
struct BriefingLensState {
    enum LoadPhase: Equatable {
        case idle
        case initial
        case continuation
        case replacingVisible
    }

    enum Failure: Equatable {
        case initial(String)
        case continuation(String)
    }

    var document: APIBriefingLensResponse?
    var renderModel: BriefingLensRenderModel?
    var loadPhase: LoadPhase = .idle
    var failure: Failure?
    var documentGeneration = 0
    var isStale = false

    var initialError: String? {
        guard case .initial(let message) = failure else { return nil }
        return message
    }

    var continuationError: String? {
        guard case .continuation(let message) = failure else { return nil }
        return message
    }

    var isLoadingContinuation: Bool {
        loadPhase == .continuation || loadPhase == .replacingVisible
    }
}
