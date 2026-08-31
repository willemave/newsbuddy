//
//  KeychainManager.swift
//  newsly
//
//  Created by Assistant on 10/25/25.
//

import Foundation
import os.log
import Security

/// Manages secure storage of authentication tokens in the iOS Keychain
final class KeychainManager: AuthTokenStore {
    static let shared = KeychainManager()

    private init() {}

    private let serviceName = "com.newsly.app"
    private let accessGroupLock = NSLock()
    private var accessGroup: String?

    enum KeychainKey: String {
        case accessToken = "accessToken"
        case refreshToken = "refreshToken"
        case userId = "userId"
        case cachedUser = "cachedUser"
        case refreshAttempt = "refreshAttempt"
        case credentialEnvelope = "credentialEnvelope"
        case credentialPublication = "credentialPublication"
    }

    /// Optional configuration for shared keychain access (e.g., extensions).
    func configure(accessGroup: String?) {
        accessGroupLock.lock()
        defer { accessGroupLock.unlock() }
        self.accessGroup = accessGroup
    }

    private let logger = Logger(subsystem: "com.newsly", category: "KeychainManager")

    /// Save a token to the keychain
    func saveToken(_ token: String, key: KeychainKey) {
        _ = saveTokenReportingStatus(token, key: key)
    }

    /// Writes every secure compatibility leg and reports whether all Keychain
    /// writes succeeded. Credential envelope publication uses this to avoid
    /// declaring a partially written pair committed.
    func saveTokenReportingStatus(_ token: String, key: KeychainKey) -> Bool {
        if E2ETestLaunch.isEnabled {
            return saveE2EToken(token, account: key.rawValue)
        }
        guard let data = token.data(using: .utf8) else { return false }

        let configuredAccessGroup = currentAccessGroup()
        let primaryStatus = upsertToken(data, account: key.rawValue, accessGroup: configuredAccessGroup)
        if primaryStatus != errSecSuccess {
            logger.error("[Keychain] Save failed | account=\(key.rawValue, privacy: .public) status=\(primaryStatus)")
        }

        var legacyStatus = errSecSuccess
        if configuredAccessGroup != nil {
            legacyStatus = upsertToken(data, account: key.rawValue, accessGroup: nil)
            if legacyStatus != errSecSuccess {
                logger.error("[Keychain] Legacy save failed | account=\(key.rawValue, privacy: .public) status=\(legacyStatus)")
            }
        }

        if shouldMirrorToSharedDefaults(key: key) {
            mirrorTokenToSharedDefaults(token, key: key)
        } else {
            clearMirroredTokenFromSharedDefaults(account: key.rawValue)
        }
        return primaryStatus == errSecSuccess && legacyStatus == errSecSuccess
    }

    /// Retrieve a token from the keychain
    func getToken(key: KeychainKey) -> String? {
        if E2ETestLaunch.isEnabled {
            return e2eToken(account: key.rawValue)
        }
        let configuredAccessGroup = currentAccessGroup()
        if let accessGroup = configuredAccessGroup,
           let token = queryToken(account: key.rawValue, accessGroup: accessGroup) {
            return token
        }

        if let legacyToken = queryToken(account: key.rawValue, accessGroup: nil) {
            if configuredAccessGroup != nil {
                saveToken(legacyToken, key: key)
            }
            return legacyToken
        }

        // Once an envelope exists, its token pair is the secure fallback. A
        // plaintext mirror must never resurrect an older credential after the
        // split Keychain values have been removed.
        let envelopeToken: String?
        if key == .accessToken || key == .refreshToken,
           let envelope = decodedCredentialEnvelope() {
            envelopeToken = key == .accessToken
                ? envelope.tokens.accessToken
                : envelope.tokens.refreshToken
        } else {
            envelopeToken = nil
        }
        let mirroredToken = mirroredTokenFromSharedDefaults(key: key)
        if let fallback = CredentialFallbackPolicy.token(
            envelope: envelopeToken,
            plaintextMirror: mirroredToken
        ) {
            if configuredAccessGroup != nil {
                // Only legacy mirror restoration self-heals here. Envelope
                // reconciliation is serialized by CredentialSession.
                if envelopeToken == nil {
                    saveToken(fallback, key: key)
                }
            }
            return fallback
        }

        return nil
    }

    /// Delete a specific token from the keychain
    func deleteToken(key: KeychainKey) {
        _ = deleteTokenReportingStatus(key: key)
    }

    /// Deletes every secure copy plus its App Group compatibility mirror and
    /// reports failure instead of treating a best-effort `SecItemDelete` as a
    /// completed logout.
    func deleteTokenReportingStatus(key: KeychainKey) -> Bool {
        deleteTokenReportingStatus(account: key.rawValue)
    }

    /// Delete a legacy token entry by account name.
    func deleteLegacyToken(named account: String) {
        _ = deleteTokenReportingStatus(account: account)
    }

    private func deleteTokenReportingStatus(account: String) -> Bool {
        if E2ETestLaunch.isEnabled {
            e2eCredentialDefaults.removeObject(forKey: account)
            return e2eCredentialDefaults.object(forKey: account) == nil
        }
        var succeeded = true
        if let accessGroup = currentAccessGroup() {
            succeeded = deletionSucceeded(
                deleteToken(account: account, accessGroup: accessGroup),
                account: account,
                location: "access-group"
            ) && succeeded
        }
        succeeded = deletionSucceeded(
            deleteToken(account: account, accessGroup: nil),
            account: account,
            location: "legacy"
        ) && succeeded
        clearMirroredTokenFromSharedDefaults(account: account)
        if SharedContainer.userDefaults.object(forKey: account) != nil {
            logger.error(
                "[Keychain] App Group mirror delete failed | account=\(account, privacy: .public)"
            )
            succeeded = false
        }
        return succeeded
    }

    func currentAccessGroup() -> String? {
        accessGroupLock.lock()
        defer { accessGroupLock.unlock() }
        return accessGroup
    }

    private func mirrorTokenToSharedDefaults(_ token: String, key: KeychainKey) {
        SharedContainer.userDefaults.set(token, forKey: key.rawValue)
        SharedContainer.userDefaults.synchronize()
    }

    func mirroredTokenFromSharedDefaults(key: KeychainKey) -> String? {
        guard shouldMirrorToSharedDefaults(key: key) else { return nil }
        return SharedContainer.userDefaults.string(forKey: key.rawValue)
    }

    private func clearMirroredTokenFromSharedDefaults(account: String) {
        SharedContainer.userDefaults.removeObject(forKey: account)
        SharedContainer.userDefaults.synchronize()
    }

    private func shouldMirrorToSharedDefaults(key: KeychainKey) -> Bool {
        shouldMirrorToSharedDefaults(account: key.rawValue)
    }

    private func shouldMirrorToSharedDefaults(account: String) -> Bool {
        account == KeychainKey.accessToken.rawValue || account == KeychainKey.refreshToken.rawValue
    }

    private func upsertToken(_ data: Data, account: String, accessGroup: String?) -> OSStatus {
        let query = baseQuery(account: account, accessGroup: accessGroup)
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            // Allow background refreshes after first unlock so timers and
            // URLSession work can read credentials.
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        let updateStatus = SecItemUpdate(
            query as CFDictionary,
            attributes as CFDictionary
        )
        guard updateStatus == errSecItemNotFound else { return updateStatus }

        var insertion = query
        for (key, value) in attributes {
            insertion[key] = value
        }
        return SecItemAdd(insertion as CFDictionary, nil)
    }

    private func queryToken(account: String, accessGroup: String?) -> String? {
        guard case .value(let value) = rawToken(
            account: account,
            accessGroup: accessGroup
        ) else {
            return nil
        }
        return value
    }

    enum RawTokenRead {
        case value(String)
        case missing
        case unavailable
    }

    func rawToken(account: String, accessGroup: String?) -> RawTokenRead {
        if E2ETestLaunch.isEnabled {
            guard let token = e2eToken(account: account) else { return .missing }
            return .value(token)
        }
        var query = baseQuery(account: account, accessGroup: accessGroup)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound {
            return .missing
        }
        guard status == errSecSuccess,
              let data = result as? Data,
              let token = String(data: data, encoding: .utf8) else {
            return .unavailable
        }
        return .value(token)
    }

    /// The native release gate builds without code signing, so Simulator
    /// Keychain calls cannot succeed. Explicit DEBUG E2E launches use the App
    /// Group defaults to exercise credential rotation, process relaunch, and
    /// extension sharing without weakening fail-closed behavior in normal builds.
    private func saveE2EToken(_ token: String, account: String) -> Bool {
        e2eCredentialDefaults.set(token, forKey: account)
        return e2eCredentialDefaults.string(forKey: account) == token
    }

    private func e2eToken(account: String) -> String? {
        e2eCredentialDefaults.string(forKey: account)
    }

    private var e2eCredentialDefaults: UserDefaults {
        guard let appGroupID = SharedContainer.appGroupId,
              let defaults = UserDefaults(suiteName: appGroupID) else {
            return .standard
        }
        return defaults
    }

    private func deleteToken(account: String, accessGroup: String?) -> OSStatus {
        SecItemDelete(baseQuery(account: account, accessGroup: accessGroup) as CFDictionary)
    }

    private func deletionSucceeded(
        _ status: OSStatus,
        account: String,
        location: String
    ) -> Bool {
        guard status == errSecSuccess || status == errSecItemNotFound else {
            logger.error(
                "[Keychain] Delete failed | account=\(account, privacy: .public) location=\(location, privacy: .public) status=\(status)"
            )
            return false
        }
        return true
    }

    private func baseQuery(account: String, accessGroup: String?) -> [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: serviceName,
            kSecAttrAccount as String: account
        ]

        if let accessGroup {
            query[kSecAttrAccessGroup as String] = accessGroup
        }

        return query
    }

    /// Clear all authentication data from the keychain
    func clearAll() {
        deleteToken(key: .accessToken)
        deleteToken(key: .refreshToken)
        deleteToken(key: .userId)
        deleteToken(key: .cachedUser)
        deleteToken(key: .refreshAttempt)
        deleteToken(key: .credentialEnvelope)
        deleteToken(key: .credentialPublication)
        deleteLegacyToken(named: "openaiApiKey")
    }

    func decodedCredentialEnvelope() -> CredentialEnvelope? {
        let accessGroup = currentAccessGroup()
        let encoded: String?
        if let accessGroup,
           case .value(let value) = rawToken(
               account: KeychainKey.credentialEnvelope.rawValue,
               accessGroup: accessGroup
           ) {
            encoded = value
        } else if case .value(let value) = rawToken(
            account: KeychainKey.credentialEnvelope.rawValue,
            accessGroup: nil
        ) {
            encoded = value
        } else {
            encoded = nil
        }
        guard let encoded,
              let data = Data(base64Encoded: encoded),
              let envelope = try? JSONDecoder().decode(CredentialEnvelope.self, from: data),
              envelope.tokens.isComplete else {
            return nil
        }
        return envelope
    }
}

protocol AuthTokenStore: AnyObject {
    func getToken(key: KeychainManager.KeychainKey) -> String?
    func saveToken(_ token: String, key: KeychainManager.KeychainKey)
    func saveTokenReportingStatus(
        _ token: String,
        key: KeychainManager.KeychainKey
    ) -> Bool
    func deleteToken(key: KeychainManager.KeychainKey)
    func deleteTokenReportingStatus(key: KeychainManager.KeychainKey) -> Bool
    func clearAll()
}

extension AuthTokenStore {
    func saveTokenReportingStatus(
        _ token: String,
        key: KeychainManager.KeychainKey
    ) -> Bool {
        saveToken(token, key: key)
        return getToken(key: key) == token
    }

    func deleteTokenReportingStatus(key: KeychainManager.KeychainKey) -> Bool {
        deleteToken(key: key)
        return getToken(key: key) == nil
    }
}
