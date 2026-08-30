import CryptoKit
import Foundation

protocol RefreshAttemptStoring: AnyObject {
    func attemptID(for refreshToken: String) throws -> String
    func clearAttempt(for refreshToken: String)
}

enum RefreshAttemptPersistenceRead: Equatable {
    case value(String)
    case missing
    case unavailable
}

enum RefreshAttemptStoreError: Error, Equatable {
    case storageUnavailable
    case invalidStoredValue
    case writeFailed
}

/// The narrow durable-storage contract required before a refresh token may be
/// exchanged. Unlike `AuthTokenStore.saveToken`, a write must report failure
/// and the stored value must be available for an immediate read-back check.
protocol RefreshAttemptPersisting: AnyObject {
    func readRefreshAttempt() -> RefreshAttemptPersistenceRead
    func persistRefreshAttempt(_ encodedEnvelope: String) throws
    func deleteRefreshAttempt()
}

/// Persists the idempotency identity before `/auth/refresh` is sent. If the
/// response is lost or the process is reclaimed, the same old token reuses the
/// same attempt ID and can retrieve the backend's one already-minted pair.
final class KeychainRefreshAttemptStore: RefreshAttemptStoring {
    private struct Envelope: Codable {
        let refreshTokenFingerprint: String
        let attemptID: String
    }

    private let persistence: any RefreshAttemptPersisting
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    init(persistence: any RefreshAttemptPersisting) {
        self.persistence = persistence
    }

    func attemptID(for refreshToken: String) throws -> String {
        let fingerprint = Self.fingerprint(refreshToken)
        switch try loadEnvelope() {
        case .some(let envelope) where envelope.refreshTokenFingerprint == fingerprint:
            return envelope.attemptID
        case .some, .none:
            break
        }

        let envelope = Envelope(
            refreshTokenFingerprint: fingerprint,
            attemptID: UUID().uuidString.lowercased()
        )
        let encodedEnvelope: String
        do {
            encodedEnvelope = try encoder.encode(envelope).base64EncodedString()
        } catch {
            throw RefreshAttemptStoreError.writeFailed
        }
        do {
            try persistence.persistRefreshAttempt(encodedEnvelope)
        } catch {
            throw RefreshAttemptStoreError.writeFailed
        }
        guard persistence.readRefreshAttempt() == .value(encodedEnvelope) else {
            throw RefreshAttemptStoreError.writeFailed
        }
        return envelope.attemptID
    }

    func clearAttempt(for refreshToken: String) {
        guard let envelope = try? loadEnvelope(),
              envelope.refreshTokenFingerprint == Self.fingerprint(refreshToken) else {
            return
        }
        persistence.deleteRefreshAttempt()
    }

    private func loadEnvelope() throws -> Envelope? {
        switch persistence.readRefreshAttempt() {
        case .missing:
            return nil
        case .unavailable:
            throw RefreshAttemptStoreError.storageUnavailable
        case .value(let encoded):
            guard let data = Data(base64Encoded: encoded),
                  let envelope = try? decoder.decode(Envelope.self, from: data),
                  !envelope.attemptID.isEmpty else {
                throw RefreshAttemptStoreError.invalidStoredValue
            }
            return envelope
        }
    }

    private static func fingerprint(_ refreshToken: String) -> String {
        SHA256.hash(data: Data(refreshToken.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
    }
}

extension KeychainManager: RefreshAttemptPersisting {
    func readRefreshAttempt() -> RefreshAttemptPersistenceRead {
        let account = KeychainKey.refreshAttempt.rawValue
        if let accessGroup = currentAccessGroup() {
            switch rawToken(account: account, accessGroup: accessGroup) {
            case .value(let value):
                return .value(value)
            case .unavailable:
                return .unavailable
            case .missing:
                break
            }
        }

        switch rawToken(account: account, accessGroup: nil) {
        case .value(let value):
            return .value(value)
        case .missing:
            return .missing
        case .unavailable:
            return .unavailable
        }
    }

    func persistRefreshAttempt(_ encodedEnvelope: String) throws {
        guard saveTokenReportingStatus(encodedEnvelope, key: .refreshAttempt) else {
            throw RefreshAttemptStoreError.writeFailed
        }
    }

    func deleteRefreshAttempt() {
        deleteToken(key: .refreshAttempt)
    }
}
