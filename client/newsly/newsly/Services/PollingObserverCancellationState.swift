//
//  PollingObserverCancellationState.swift
//  newsly
//

import Foundation

/// Bridges task cancellation into an actor subscription without leaving race-prone tombstones.
final class PollingObserverCancellationState: @unchecked Sendable {
    private let lock = NSLock()
    private var cancelled = false

    var isCancelled: Bool {
        lock.withLock { cancelled }
    }

    func cancel() {
        lock.withLock { cancelled = true }
    }
}

/// Shared actor-confined storage for keyed polling registries.
///
/// Domain registries still own fetch cadence and terminal-state semantics, while this
/// type owns observer coalescing, generation checks, orphan handoff, and task teardown.
struct KeyedPollingObserverStore<Key: Hashable, Output, Metadata> {
    struct Observer {
        let continuation: CheckedContinuation<Output, Error>
        let metadata: Metadata
    }

    struct Entry {
        let generation: UUID
        var observers: [UUID: Observer]
        var pollingTask: Task<Void, Never>?
        var orphanCancellationTask: Task<Void, Never>?
    }

    private var entries: [Key: Entry] = [:]

    func observerCount(for key: Key) -> Int {
        entries[key]?.observers.count ?? 0
    }

    func isActive(key: Key, generation: UUID) -> Bool {
        entries[key]?.generation == generation
    }

    func observers(for key: Key, generation: UUID) -> [Observer] {
        guard let entry = entries[key], entry.generation == generation else { return [] }
        return Array(entry.observers.values)
    }

    /// Adds an observer and returns a generation only when a new poll must start.
    mutating func addObserver(
        _ observer: Observer,
        id observerID: UUID,
        for key: Key
    ) -> UUID? {
        if var entry = entries[key] {
            entry.orphanCancellationTask?.cancel()
            entry.orphanCancellationTask = nil
            entry.observers[observerID] = observer
            entries[key] = entry
            return nil
        }

        let generation = UUID()
        entries[key] = Entry(
            generation: generation,
            observers: [observerID: observer],
            pollingTask: nil,
            orphanCancellationTask: nil
        )
        return generation
    }

    mutating func setPollingTask(
        _ task: Task<Void, Never>,
        for key: Key,
        generation: UUID
    ) {
        guard var entry = entries[key], entry.generation == generation else {
            task.cancel()
            return
        }
        entry.pollingTask = task
        entries[key] = entry
    }

    /// Removes one observer and returns a generation when the poll becomes orphaned.
    mutating func removeObserver(
        id observerID: UUID,
        for key: Key
    ) -> (observer: Observer, orphanedGeneration: UUID?)? {
        guard var entry = entries[key],
              let observer = entry.observers.removeValue(forKey: observerID) else {
            return nil
        }
        let orphanedGeneration = entry.observers.isEmpty ? entry.generation : nil
        entries[key] = entry
        return (observer, orphanedGeneration)
    }

    mutating func setOrphanCancellationTask(
        _ task: Task<Void, Never>,
        for key: Key,
        generation: UUID
    ) {
        guard var entry = entries[key],
              entry.generation == generation,
              entry.observers.isEmpty else {
            task.cancel()
            return
        }
        entry.orphanCancellationTask?.cancel()
        entry.orphanCancellationTask = task
        entries[key] = entry
    }

    mutating func removeIfOrphaned(
        key: Key,
        generation: UUID
    ) -> Task<Void, Never>? {
        guard let entry = entries[key],
              entry.generation == generation,
              entry.observers.isEmpty else {
            return nil
        }
        entries.removeValue(forKey: key)
        return entry.pollingTask
    }

    mutating func removeEntry(for key: Key, generation: UUID) -> Entry? {
        guard let entry = entries[key], entry.generation == generation else { return nil }
        entries.removeValue(forKey: key)
        entry.orphanCancellationTask?.cancel()
        return entry
    }

    mutating func removeEntry(for key: Key) -> Entry? {
        guard let entry = entries.removeValue(forKey: key) else { return nil }
        entry.pollingTask?.cancel()
        entry.orphanCancellationTask?.cancel()
        return entry
    }
}
