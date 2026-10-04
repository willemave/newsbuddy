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
    private let keychain: any AuthTokenStore
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    init(keychain: any AuthTokenStore) {
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
                keychain.mirroredCredentialToken(key: .accessToken)
            )
            let mirrorRefresh = nonEmpty(
                keychain.mirroredCredentialToken(key: .refreshToken)
            )
            let material = LegacyCredentialMaterial(
                accessToken: mirrorAccess,
                refreshToken: mirrorRefresh,
                isCoherentPair: mirrorAccess != nil && mirrorRefresh != nil
            )
            return material.hasAnyToken ? .value(material) : .missing
        }

        let mirrorAccess = nonEmpty(
            keychain.mirroredCredentialToken(key: .accessToken)
        )
        let mirrorRefresh = nonEmpty(
            keychain.mirroredCredentialToken(key: .refreshToken)
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
    ) -> CredentialStoreRead<RawCredentialToken> {
        keychain.rawCredentialToken(key: key)
    }

    private func envelopeSource() -> CredentialTokenSource? {
        keychain.rawCredentialToken(key: .credentialEnvelope).value?.source
    }

    private func rawKeychainValue(
        for key: KeychainManager.KeychainKey
    ) -> KeychainManager.RawTokenRead {
        switch keychain.rawCredentialToken(key: key) {
        case .value(let token):
            return .value(token.value)
        case .missing:
            return .missing
        case .unavailable:
            return .unavailable
        }
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
