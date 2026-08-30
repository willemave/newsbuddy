import Foundation

/// The token pair published by sign-in or refresh. Keeping the pair in one
/// value prevents callers from inventing their own write order.
struct CredentialTokens: Codable, Equatable, Sendable {
    let accessToken: String
    let refreshToken: String

    var isComplete: Bool {
        !accessToken.isEmpty && !refreshToken.isEmpty
    }
}

/// The canonical secure credential record for new app and extension builds.
/// A generation identifies one session publication, not one access-token use.
struct CredentialEnvelope: Codable, Equatable, Sendable {
    let tokens: CredentialTokens
    let userID: Int
    let generation: UUID

    init(
        tokens: CredentialTokens,
        userID: Int,
        generation: UUID = UUID()
    ) {
        self.tokens = tokens
        self.userID = userID
        self.generation = generation
    }
}

/// Loose material written by already-distributed builds. It cannot establish
/// identity until `/auth/me` validates it and promotes it into an envelope.
struct LegacyCredentialMaterial: Equatable, Sendable {
    enum EnvelopeRelationship: Equatable {
        case matching
        case coherentTakeover
        case repairableIncomplete
        case unsafeDivergence
    }

    let accessToken: String?
    let refreshToken: String?
    let needsReconciliation: Bool
    let isCoherentPair: Bool

    init(
        accessToken: String?,
        refreshToken: String?,
        needsReconciliation: Bool = false,
        isCoherentPair: Bool? = nil
    ) {
        self.accessToken = accessToken
        self.refreshToken = refreshToken
        self.needsReconciliation = needsReconciliation
        self.isCoherentPair = isCoherentPair
            ?? (accessToken?.isEmpty == false && refreshToken?.isEmpty == false)
    }

    var hasAnyToken: Bool {
        accessToken?.isEmpty == false || refreshToken?.isEmpty == false
    }

    var completeTokens: CredentialTokens? {
        guard let accessToken, !accessToken.isEmpty,
              let refreshToken, !refreshToken.isEmpty else {
            return nil
        }
        return CredentialTokens(
            accessToken: accessToken,
            refreshToken: refreshToken
        )
    }

    func isCoherentTakeover(of envelope: CredentialEnvelope) -> Bool {
        relationship(to: envelope) == .coherentTakeover
    }

    func relationship(to envelope: CredentialEnvelope) -> EnvelopeRelationship {
        if let completeTokens {
            if completeTokens == envelope.tokens {
                return .matching
            }
            if isCoherentPair,
               completeTokens.accessToken != envelope.tokens.accessToken,
               completeTokens.refreshToken != envelope.tokens.refreshToken {
                return .coherentTakeover
            }
            // A single changed leg is an interrupted publication. In
            // particular, old-access/new-refresh can mean the old refresh was
            // already consumed, so writing the envelope pair back would strand
            // the session.
            return .unsafeDivergence
        }

        let accessDiverges = accessToken?.isEmpty == false
            && accessToken != envelope.tokens.accessToken
        let refreshDiverges = refreshToken?.isEmpty == false
            && refreshToken != envelope.tokens.refreshToken
        return accessDiverges || refreshDiverges
            ? .unsafeDivergence
            : .repairableIncomplete
    }
}

enum CredentialStoreRead<Value> {
    case value(Value)
    case missing
    case unavailable
}

/// Launch-time credential fact. `unavailable` is deliberately separate from
/// `missing`: a temporary Keychain read failure is not evidence of logout.
enum CredentialMaterialAvailability: Equatable {
    case present
    case missing
    case unavailable
}

enum CredentialStorageError: Error, Equatable {
    case unavailable
    case writeFailed
    case deleteFailed
}

enum CredentialFallbackPolicy {
    /// A secure envelope always outranks the plaintext compatibility mirror.
    /// The mirror is considered only before an envelope has ever been written.
    static func token(envelope: String?, plaintextMirror: String?) -> String? {
        envelope ?? plaintextMirror
    }
}
