//
//  TaskBag.swift
//  newsly
//

import Foundation

final class TaskBag<Key: Hashable> {
    struct Token {
        fileprivate let key: Key
        let generation: Int
    }

    private var tasks: [Key: Task<Void, Never>] = [:]
    private var generations: [Key: Int] = [:]

    deinit {
        cancelAll()
    }

    func isRunning(_ key: Key) -> Bool {
        tasks[key] != nil
    }

    func task(for key: Key) -> Task<Void, Never>? {
        tasks[key]
    }

    func isCurrent(_ token: Token) -> Bool {
        generations[token.key] == token.generation && tasks[token.key] != nil
    }

    func cancel(_ key: Key) {
        generations[key, default: 0] += 1
        tasks[key]?.cancel()
        tasks[key] = nil
    }

    func cancelAll() {
        for (key, task) in tasks {
            generations[key, default: 0] += 1
            task.cancel()
        }
        tasks.removeAll()
    }

    @discardableResult
    func runReplacing(
        _ key: Key,
        operation: @escaping @MainActor () async -> Void
    ) -> Task<Void, Never> {
        runReplacing(key) { _ in
            await operation()
        }
    }

    @discardableResult
    func runReplacing(
        _ key: Key,
        operation: @escaping @MainActor (Token) async -> Void
    ) -> Task<Void, Never> {
        tasks[key]?.cancel()
        let generation = generations[key, default: 0] + 1
        generations[key] = generation
        let token = Token(key: key, generation: generation)

        let task = Task { @MainActor [weak self] in
            await operation(token)
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
