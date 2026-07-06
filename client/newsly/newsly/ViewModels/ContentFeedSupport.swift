//
//  ContentFeedSupport.swift
//  newsly
//

import Foundation

@MainActor
final class FeedLoadTaskRunner {
    private var task: Task<Void, Never>?
    private var generation = 0

    var isRunning: Bool {
        task != nil
    }

    func cancel() {
        generation += 1
        task?.cancel()
        task = nil
    }

    func runReplacing(_ operation: @escaping @MainActor () async -> Void) async {
        task?.cancel()
        generation += 1
        let currentGeneration = generation
        let nextTask = Task { @MainActor in
            await operation()
        }
        task = nextTask
        await nextTask.value
        if generation == currentGeneration {
            task = nil
        }
    }

    func runIfIdle(_ operation: @escaping @MainActor () async -> Void) async {
        guard task == nil else { return }
        await runReplacing(operation)
    }
}

@MainActor
protocol ContentSummaryFeedEditing: AnyObject {
    var contents: [ContentSummary] { get set }
}

extension ContentSummaryFeedEditing {
    func currentItems() -> [ContentSummary] {
        contents
    }

    func updateItem(id: Int, transform: (ContentSummary) -> ContentSummary) {
        guard let index = contents.firstIndex(where: { $0.id == id }) else { return }
        contents[index] = transform(contents[index])
    }

    func replaceItems(_ newItems: [ContentSummary]) {
        contents = newItems
    }
}
