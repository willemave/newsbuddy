//
//  TaskBag.swift
//  newsly
//

import Foundation

final class TaskBag<Key: Hashable> {
    private var tasks: [Key: Task<Void, Never>] = [:]
    private var generations: [Key: Int] = [:]

    deinit {
        cancelAll()
    }

    func isRunning(_ key: Key) -> Bool {
        tasks[key] != nil
    }

    func cancel(_ key: Key) {
        generations[key, default: 0] += 1
        tasks[key]?.cancel()
        tasks[key] = nil
    }

    func cancelAll() {
        for task in tasks.values {
            task.cancel()
        }
        tasks.removeAll()
        generations.removeAll()
    }

    @discardableResult
    func runReplacing(
        _ key: Key,
        operation: @escaping @MainActor () async -> Void
    ) -> Task<Void, Never> {
        cancel(key)
        let generation = generations[key, default: 0] + 1
        generations[key] = generation

        let task = Task { @MainActor [weak self] in
            await operation()
            self?.clear(key, generation: generation)
        }
        tasks[key] = task
        return task
    }

    @discardableResult
    func runIfIdle(
        _ key: Key,
        operation: @escaping @MainActor () async -> Void
    ) -> Task<Void, Never>? {
        guard !isRunning(key) else { return nil }
        return runReplacing(key, operation: operation)
    }

    private func clear(_ key: Key, generation: Int) {
        guard generations[key] == generation else { return }
        tasks[key] = nil
    }
}
