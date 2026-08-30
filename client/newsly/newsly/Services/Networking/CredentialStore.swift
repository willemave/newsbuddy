import Foundation

protocol CredentialMaterialStoring: AnyObject {
    func readEnvelope() -> CredentialStoreRead<CredentialEnvelope>
    func readLegacyMaterial() -> CredentialStoreRead<LegacyCredentialMaterial>
    func publishEnvelopeAndLegacy(_ envelope: CredentialEnvelope) throws
    func publishLegacy(_ tokens: CredentialTokens) throws
    func deleteCredentialMaterial() throws
}

extension CredentialMaterialStoring {
    func credentialAvailability() -> CredentialMaterialAvailability {
        let envelopeRead = readEnvelope()
        let legacyRead = readLegacyMaterial()

        guard !envelopeRead.isUnavailable, !legacyRead.isUnavailable else {
            return .unavailable
        }

        let envelope = envelopeRead.value
        let legacy = legacyRead.value
        if envelope != nil || legacy?.hasAnyToken == true {
            return .present
        }
        return .missing
    }

    /// Synchronous launch-time check used only to decide whether a cached user
    /// shell may be painted. A coherent two-leg legacy takeover removes
    /// confirmation until the server validates it. An interrupted one-leg
    /// publication cannot claim another identity; request resolution fails
    /// unavailable without overwriting either leg.
    func confirmedUserID() -> Int? {
        guard case .value(let envelope) = readEnvelope() else { return nil }
        switch readLegacyMaterial() {
        case .value(let legacy):
            guard !legacy.isCoherentTakeover(of: envelope) else { return nil }
        case .missing:
            break
        case .unavailable:
            return nil
        }
        return envelope.userID
    }
}

private extension CredentialStoreRead {
    var value: Value? {
        guard case .value(let value) = self else { return nil }
        return value
    }

    var isUnavailable: Bool {
        if case .unavailable = self { return true }
        return false
    }
}

/// Production storage can inspect each legacy leg without invoking getToken's
/// self-healing fallback. This is what prevents a stale plaintext mirror from
/// becoming authoritative again after the envelope exists.
final class KeychainCredentialStorage: CredentialMaterialStoring {
    private enum LegacyTokenSource: Equatable {
        case accessGroup
        case legacyKeychain
    }

    private struct SourcedLegacyToken {
        let value: String
        let source: LegacyTokenSource
    }

    private let keychain: KeychainManager
    private let decoder = JSONDecoder()

    init(keychain: KeychainManager) {
        self.keychain = keychain
    }

    func readEnvelope() -> CredentialStoreRead<CredentialEnvelope> {
        switch rawKeychainValue(for: .credentialEnvelope) {
        case .value(let encoded):
            guard let data = Data(base64Encoded: encoded),
                  let envelope = try? decoder.decode(CredentialEnvelope.self, from: data),
                  envelope.tokens.isComplete else {
                return .unavailable
            }
            return .value(envelope)
        case .missing:
            return .missing
        case .unavailable:
            return .unavailable
        }
    }

    func readLegacyMaterial() -> CredentialStoreRead<LegacyCredentialMaterial> {
        let accessRead = sourcedLegacyToken(for: .accessToken)
        let refreshRead = sourcedLegacyToken(for: .refreshToken)
        guard !accessRead.isUnavailable, !refreshRead.isUnavailable else {
            return .unavailable
        }

        let keychainAccess = accessRead.value
        let keychainRefresh = refreshRead.value
        let envelopeRead = readEnvelope()
        if case .unavailable = envelopeRead {
            return .unavailable
        }
        let confirmedEnvelopeSource = envelopeRead.value == nil ? nil : envelopeSource()

        if keychainAccess == nil, keychainRefresh == nil {
            // A valid envelope suppresses the plaintext fallback. Its pair will
            // be repaired under the process lock by CredentialSession.
            if case .value = envelopeRead {
                return .missing
            }

            let mirrorAccess = nonEmpty(
                keychain.mirroredTokenFromSharedDefaults(key: .accessToken)
            )
            let mirrorRefresh = nonEmpty(
                keychain.mirroredTokenFromSharedDefaults(key: .refreshToken)
            )
            let material = LegacyCredentialMaterial(
                accessToken: mirrorAccess,
                refreshToken: mirrorRefresh,
                isCoherentPair: mirrorAccess != nil && mirrorRefresh != nil
            )
            return material.hasAnyToken ? .value(material) : .missing
        }

        let mirrorAccess = nonEmpty(
            keychain.mirroredTokenFromSharedDefaults(key: .accessToken)
        )
        let mirrorRefresh = nonEmpty(
            keychain.mirroredTokenFromSharedDefaults(key: .refreshToken)
        )
        return .value(
            LegacyCredentialMaterial(
                accessToken: nonEmpty(keychainAccess?.value),
                refreshToken: nonEmpty(keychainRefresh?.value),
                needsReconciliation: mirrorAccess != nonEmpty(keychainAccess?.value)
                    || mirrorRefresh != nonEmpty(keychainRefresh?.value),
                isCoherentPair: keychainAccess?.source == keychainRefresh?.source
                    && keychainAccess != nil
                    && keychainRefresh != nil
                    && (confirmedEnvelopeSource == nil
                        || keychainAccess?.source == confirmedEnvelopeSource)
            )
        )
    }

    func publishEnvelopeAndLegacy(_ envelope: CredentialEnvelope) throws {
        guard envelope.tokens.isComplete,
              let data = try? JSONEncoder().encode(envelope) else {
            throw CredentialStorageError.writeFailed
        }
        guard keychain.saveTokenReportingStatus(
            envelope.tokens.refreshToken,
            key: .refreshToken
        ), keychain.saveTokenReportingStatus(
            envelope.tokens.accessToken,
            key: .accessToken
        ), keychain.saveTokenReportingStatus(
            String(envelope.userID),
            key: .userId
        ), keychain.saveTokenReportingStatus(
            data.base64EncodedString(),
            key: .credentialEnvelope
        ) else {
            throw CredentialStorageError.writeFailed
        }
    }

    func publishLegacy(_ tokens: CredentialTokens) throws {
        guard tokens.isComplete,
              keychain.saveTokenReportingStatus(tokens.refreshToken, key: .refreshToken),
              keychain.saveTokenReportingStatus(tokens.accessToken, key: .accessToken) else {
            throw CredentialStorageError.writeFailed
        }
    }

    func deleteCredentialMaterial() throws {
        let keys: [KeychainManager.KeychainKey] = [
            .credentialEnvelope,
            .accessToken,
            .refreshToken,
            .userId,
            .refreshAttempt,
        ]
        let outcomes = keys.map { keychain.deleteTokenReportingStatus(key: $0) }
        guard outcomes.allSatisfy({ $0 }) else {
            throw CredentialStorageError.deleteFailed
        }
    }

    private func sourcedLegacyToken(
        for key: KeychainManager.KeychainKey
    ) -> CredentialStoreRead<SourcedLegacyToken> {
        if let accessGroup = keychain.currentAccessGroup() {
            switch keychain.rawToken(account: key.rawValue, accessGroup: accessGroup) {
            case .value(let value):
                return .value(SourcedLegacyToken(value: value, source: .accessGroup))
            case .unavailable:
                return .unavailable
            case .missing:
                break
            }
        }
        switch keychain.rawToken(account: key.rawValue, accessGroup: nil) {
        case .value(let value):
            return .value(SourcedLegacyToken(value: value, source: .legacyKeychain))
        case .missing:
            return .missing
        case .unavailable:
            return .unavailable
        }
    }

    private func envelopeSource() -> LegacyTokenSource? {
        let key = KeychainManager.KeychainKey.credentialEnvelope
        if let accessGroup = keychain.currentAccessGroup() {
            switch keychain.rawToken(account: key.rawValue, accessGroup: accessGroup) {
            case .value:
                return .accessGroup
            case .missing:
                break
            case .unavailable:
                return nil
            }
        }
        if case .value = keychain.rawToken(account: key.rawValue, accessGroup: nil) {
            return .legacyKeychain
        }
        return nil
    }

    private func rawKeychainValue(
        for key: KeychainManager.KeychainKey
    ) -> KeychainManager.RawTokenRead {
        if let accessGroup = keychain.currentAccessGroup() {
            switch keychain.rawToken(account: key.rawValue, accessGroup: accessGroup) {
            case .value(let value):
                return .value(value)
            case .unavailable:
                return .unavailable
            case .missing:
                break
            }
        }
        return keychain.rawToken(account: key.rawValue, accessGroup: nil)
    }

    private func nonEmpty(_ value: String?) -> String? {
        guard let value, !value.isEmpty else { return nil }
        return value
    }
}

private extension KeychainManager.RawTokenRead {
    var value: String? {
        guard case .value(let value) = self else { return nil }
        return value
    }

    var isUnavailable: Bool {
        if case .unavailable = self { return true }
        return false
    }
}

/// Transitional adapter for injected token stores. Production Keychain storage
/// adds raw-keychain/mirror reconciliation; tests and older adapters still get
/// the same envelope and refresh-token-first publication contract.
final class TokenStoreCredentialStorage: CredentialMaterialStoring {
    private let tokenStore: any AuthTokenStore
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    init(tokenStore: any AuthTokenStore) {
        self.tokenStore = tokenStore
    }

    func readEnvelope() -> CredentialStoreRead<CredentialEnvelope> {
        guard let encoded = tokenStore.getToken(key: .credentialEnvelope) else {
            return .missing
        }
        guard let data = Data(base64Encoded: encoded),
              let envelope = try? decoder.decode(CredentialEnvelope.self, from: data),
              envelope.tokens.isComplete else {
            return .unavailable
        }
        return .value(envelope)
    }

    func readLegacyMaterial() -> CredentialStoreRead<LegacyCredentialMaterial> {
        let material = LegacyCredentialMaterial(
            accessToken: nonEmpty(tokenStore.getToken(key: .accessToken)),
            refreshToken: nonEmpty(tokenStore.getToken(key: .refreshToken))
        )
        return material.hasAnyToken ? .value(material) : .missing
    }

    func publishEnvelopeAndLegacy(_ envelope: CredentialEnvelope) throws {
        guard envelope.tokens.isComplete,
              let data = try? encoder.encode(envelope) else {
            throw CredentialStorageError.writeFailed
        }

        // Existing builds can observe these split values. Publish the one-time
        // refresh credential first, then access, and make the envelope visible
        // last as the atomic commit marker for new builds.
        tokenStore.saveToken(envelope.tokens.refreshToken, key: .refreshToken)
        tokenStore.saveToken(envelope.tokens.accessToken, key: .accessToken)
        tokenStore.saveToken(String(envelope.userID), key: .userId)
        tokenStore.saveToken(data.base64EncodedString(), key: .credentialEnvelope)
    }

    func publishLegacy(_ tokens: CredentialTokens) throws {
        guard tokens.isComplete else {
            throw CredentialStorageError.writeFailed
        }
        tokenStore.saveToken(tokens.refreshToken, key: .refreshToken)
        tokenStore.saveToken(tokens.accessToken, key: .accessToken)
    }

    func deleteCredentialMaterial() throws {
        let keys: [KeychainManager.KeychainKey] = [
            .credentialEnvelope,
            .accessToken,
            .refreshToken,
            .userId,
            .refreshAttempt,
        ]
        let outcomes = keys.map { tokenStore.deleteTokenReportingStatus(key: $0) }
        guard outcomes.allSatisfy({ $0 }) else {
            throw CredentialStorageError.deleteFailed
        }
    }

    private func nonEmpty(_ value: String?) -> String? {
        guard let value, !value.isEmpty else { return nil }
        return value
    }
}

enum CredentialStorageFactory {
    static func make(tokenStore: any AuthTokenStore) -> any CredentialMaterialStoring {
        if let keychain = tokenStore as? KeychainManager {
            return KeychainCredentialStorage(keychain: keychain)
        }
        return TokenStoreCredentialStorage(tokenStore: tokenStore)
    }
}
