import Foundation

struct AuthoritativeMutationLog<ID: Hashable, Value> {
    private enum Mutation {
        case upsert(Value)
        case remove
    }

    private struct Entry {
        let revision: Int
        let mutation: Mutation
    }

    private var revision = 0
    private var entries: [ID: Entry] = [:]

    var currentRevision: Int { revision }

    mutating func recordUpsert(_ value: Value, id: ID) {
        record(.upsert(value), id: id)
    }

    mutating func recordRemoval(id: ID) {
        record(.remove, id: id)
    }

    mutating func reconcile(
        authoritative values: [Value],
        requestStartRevision: Int,
        id: (Value) -> ID
    ) -> [Value] {
        entries = entries.filter { $0.value.revision > requestStartRevision }
        var reconciled = values
        for (entryID, entry) in entries.sorted(by: { $0.value.revision < $1.value.revision }) {
            switch entry.mutation {
            case .upsert(let value):
                if let index = reconciled.firstIndex(where: { id($0) == entryID }) {
                    reconciled[index] = value
                } else {
                    reconciled.insert(value, at: 0)
                }
            case .remove:
                reconciled.removeAll { id($0) == entryID }
            }
        }
        return reconciled
    }

    private mutating func record(_ mutation: Mutation, id: ID) {
        revision &+= 1
        entries[id] = Entry(revision: revision, mutation: mutation)
    }
}
