import Foundation

protocol CredentialMaterialStoring: AnyObject {
    func readEnvelope() -> CredentialStoreRead<CredentialEnvelope>
    func readLegacyMaterial() -> CredentialStoreRead<LegacyCredentialMaterial>
    func readPendingPublication() -> CredentialStoreRead<CredentialPublication>
    func publishEnvelopeAndLegacy(_ envelope: CredentialEnvelope) throws
    func completePendingPublication(_ publication: CredentialPublication) throws
    func publishLegacy(_ tokens: CredentialTokens) throws
    func deleteCredentialMaterial() throws
}

extension CredentialMaterialStoring {
    func credentialAvailability() -> CredentialMaterialAvailability {
        let pendingRead = readPendingPublication()
        let envelopeRead = readEnvelope()
        let legacyRead = readLegacyMaterial()

        guard !pendingRead.isUnavailable,
              !envelopeRead.isUnavailable,
              !legacyRead.isUnavailable else {
            return .unavailable
        }

        if pendingRead.value != nil {
            return .present
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
        guard case .missing = readPendingPublication() else { return nil }
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
    private let encoder = JSONEncoder()
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

    func readPendingPublication() -> CredentialStoreRead<CredentialPublication> {
        switch rawKeychainValue(for: .credentialPublication) {
        case .value(let encoded):
            guard let data = Data(base64Encoded: encoded),
                  let publication = try? decoder.decode(
                      CredentialPublication.self,
                      from: data
                  ),
                  publication.target.tokens.isComplete else {
                return .unavailable
            }
            return .value(publication)
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
        guard envelope.tokens.isComplete else {
            throw CredentialStorageError.writeFailed
        }
        guard case .missing = readPendingPublication() else {
            throw CredentialStorageError.unavailable
        }
        let publication = try makePublication(target: envelope)
        guard let data = try? encoder.encode(publication),
              keychain.saveTokenReportingStatus(
                  data.base64EncodedString(),
                  key: .credentialPublication
              ) else {
            throw CredentialStorageError.writeFailed
        }
        try completePendingPublication(publication)
    }

    func completePendingPublication(_ publication: CredentialPublication) throws {
        guard case .value(let stagedPublication) = readPendingPublication(),
              stagedPublication == publication else {
            throw CredentialStorageError.unavailable
        }
        let snapshot = try credentialSnapshot()
        if publication.permits(snapshot) {
            try writeCommittedEnvelope(publication.target)
        } else if !publication.isClearlySuperseded(by: snapshot) {
            throw CredentialStorageError.unavailable
        }
        guard keychain.deleteTokenReportingStatus(key: .credentialPublication) else {
            throw CredentialStorageError.writeFailed
        }
    }

    private func writeCommittedEnvelope(_ envelope: CredentialEnvelope) throws {
        guard let data = try? encoder.encode(envelope) else {
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
            .credentialPublication,
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

    private func makePublication(target: CredentialEnvelope) throws -> CredentialPublication {
        let baseline = try credentialSnapshot()
        return CredentialPublication(
            target: target,
            baselineEnvelope: baseline.envelope,
            baselineAccessToken: baseline.accessToken,
            baselineRefreshToken: baseline.refreshToken,
            baselineUserID: baseline.userID
        )
    }

    private func credentialSnapshot() throws -> CredentialPublicationSnapshot {
        let envelope: CredentialEnvelope?
        switch readEnvelope() {
        case .value(let value):
            envelope = value
        case .missing:
            envelope = nil
        case .unavailable:
            throw CredentialStorageError.unavailable
        }

        let legacy: LegacyCredentialMaterial?
        switch readLegacyMaterial() {
        case .value(let value):
            legacy = value
        case .missing:
            legacy = nil
        case .unavailable:
            throw CredentialStorageError.unavailable
        }

        let userID: String?
        switch rawKeychainValue(for: .userId) {
        case .value(let value):
            userID = nonEmpty(value)
        case .missing:
            userID = nil
        case .unavailable:
            throw CredentialStorageError.unavailable
        }
        return CredentialPublicationSnapshot(
            envelope: envelope,
            accessToken: legacy?.accessToken,
            refreshToken: legacy?.refreshToken,
            userID: userID
        )
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

    func readPendingPublication() -> CredentialStoreRead<CredentialPublication> {
        guard let encoded = tokenStore.getToken(key: .credentialPublication) else {
            return .missing
        }
        guard let data = Data(base64Encoded: encoded),
              let publication = try? decoder.decode(
                  CredentialPublication.self,
                  from: data
              ),
              publication.target.tokens.isComplete else {
            return .unavailable
        }
        return .value(publication)
    }

    func readLegacyMaterial() -> CredentialStoreRead<LegacyCredentialMaterial> {
        let material = LegacyCredentialMaterial(
            accessToken: nonEmpty(tokenStore.getToken(key: .accessToken)),
            refreshToken: nonEmpty(tokenStore.getToken(key: .refreshToken))
        )
        return material.hasAnyToken ? .value(material) : .missing
    }

    func publishEnvelopeAndLegacy(_ envelope: CredentialEnvelope) throws {
        guard envelope.tokens.isComplete else {
            throw CredentialStorageError.writeFailed
        }
        guard case .missing = readPendingPublication() else {
            throw CredentialStorageError.unavailable
        }
        let baseline = try credentialSnapshot()
        let publication = CredentialPublication(
            target: envelope,
            baselineEnvelope: baseline.envelope,
            baselineAccessToken: baseline.accessToken,
            baselineRefreshToken: baseline.refreshToken,
            baselineUserID: baseline.userID
        )
        guard let data = try? encoder.encode(publication),
              tokenStore.saveTokenReportingStatus(
                  data.base64EncodedString(),
                  key: .credentialPublication
              ) else {
            throw CredentialStorageError.writeFailed
        }
        try completePendingPublication(publication)
    }

    func completePendingPublication(_ publication: CredentialPublication) throws {
        guard case .value(let stagedPublication) = readPendingPublication(),
              stagedPublication == publication else {
            throw CredentialStorageError.unavailable
        }
        let snapshot = try credentialSnapshot()
        if !publication.permits(snapshot) {
            guard publication.isClearlySuperseded(by: snapshot),
                  tokenStore.deleteTokenReportingStatus(
                      key: .credentialPublication
                  ) else {
                throw CredentialStorageError.unavailable
            }
            return
        }
        guard let envelopeData = try? encoder.encode(publication.target) else {
            throw CredentialStorageError.writeFailed
        }

        // Existing builds can observe these split values. Publish the one-time
        // refresh credential first, then access, and make the envelope visible
        // last as the atomic commit marker for new builds.
        guard tokenStore.saveTokenReportingStatus(
            publication.target.tokens.refreshToken,
            key: .refreshToken
        ), tokenStore.saveTokenReportingStatus(
            publication.target.tokens.accessToken,
            key: .accessToken
        ), tokenStore.saveTokenReportingStatus(
            String(publication.target.userID),
            key: .userId
        ), tokenStore.saveTokenReportingStatus(
            envelopeData.base64EncodedString(),
            key: .credentialEnvelope
        ), tokenStore.deleteTokenReportingStatus(key: .credentialPublication) else {
            throw CredentialStorageError.writeFailed
        }
    }

    func publishLegacy(_ tokens: CredentialTokens) throws {
        guard tokens.isComplete else {
            throw CredentialStorageError.writeFailed
        }
        guard tokenStore.saveTokenReportingStatus(tokens.refreshToken, key: .refreshToken),
              tokenStore.saveTokenReportingStatus(tokens.accessToken, key: .accessToken) else {
            throw CredentialStorageError.writeFailed
        }
    }

    func deleteCredentialMaterial() throws {
        let keys: [KeychainManager.KeychainKey] = [
            .credentialPublication,
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

    private func credentialSnapshot() throws -> CredentialPublicationSnapshot {
        let envelopeRead = readEnvelope()
        let legacyRead = readLegacyMaterial()
        guard !envelopeRead.isUnavailable, !legacyRead.isUnavailable else {
            throw CredentialStorageError.unavailable
        }
        return CredentialPublicationSnapshot(
            envelope: envelopeRead.value,
            accessToken: legacyRead.value?.accessToken,
            refreshToken: legacyRead.value?.refreshToken,
            userID: nonEmpty(tokenStore.getToken(key: .userId))
        )
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
