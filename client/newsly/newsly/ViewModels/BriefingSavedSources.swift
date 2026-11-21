import Foundation
import Observation

/// Knowledge saves started from Briefing in this session.
///
/// Saved state is deliberately session-local: the Briefing index carries no
/// saved flag, because every Knowledge writer would then have to change the
/// Briefing version and force a refetch of every loaded lens. Saving is
/// idempotent, so a reset after relaunch only re-offers a harmless action.
/// Each source saves independently; a pending save never blocks another card.
@MainActor
@Observable
final class BriefingSavedSources {
    enum Outcome: Equatable {
        case saved(message: String)
        case failed(message: String)
        case ignored
    }

    private var savedSourceKeys: Set<String> = []
    private var pendingSaves: [String: Task<String, Error>] = [:]

    @ObservationIgnored
    private(set) var isDetached = false

    @ObservationIgnored
    private let saveSource: (KnowledgeSource) async throws -> String

    init(saveSource: @escaping (KnowledgeSource) async throws -> String) {
        self.saveSource = saveSource
    }

    func isSaved(_ sourceKey: String) -> Bool {
        savedSourceKeys.contains(sourceKey)
    }

    func isPending(_ sourceKey: String) -> Bool {
        pendingSaves[sourceKey] != nil
    }

    func save(sourceKey: String, source: KnowledgeSource) async -> Outcome {
        guard !isDetached, !isPending(sourceKey), !Task.isCancelled else { return .ignored }
        let task = Task { [saveSource] in
            try Task.checkCancellation()
            return try await saveSource(source)
        }
        pendingSaves[sourceKey] = task
        defer { pendingSaves[sourceKey] = nil }

        do {
            let message = try await withTaskCancellationHandler {
                try await task.value
            } onCancel: {
                task.cancel()
            }
            guard !isDetached else { return .ignored }
            // The server accepted the save even if the caller went away.
            savedSourceKeys.insert(sourceKey)
            return Task.isCancelled ? .ignored : .saved(message: message)
        } catch {
            guard !isDetached, !Task.isCancelled, ClientFailure.classify(error) != .cancelled else {
                return .ignored
            }
            return .failed(message: error.localizedDescription)
        }
    }

    func detach() {
        isDetached = true
        for task in pendingSaves.values {
            task.cancel()
        }
        pendingSaves.removeAll()
        savedSourceKeys.removeAll()
    }
}
