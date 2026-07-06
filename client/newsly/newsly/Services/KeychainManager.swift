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
        guard let data = token.data(using: .utf8) else { return }

        let configuredAccessGroup = currentAccessGroup()
        let primaryStatus = upsertToken(data, account: key.rawValue, accessGroup: configuredAccessGroup)
        if primaryStatus != errSecSuccess {
            logger.error("[Keychain] Save failed | account=\(key.rawValue, privacy: .public) status=\(primaryStatus)")
        }

        if configuredAccessGroup != nil {
            let legacyStatus = upsertToken(data, account: key.rawValue, accessGroup: nil)
            if legacyStatus != errSecSuccess {
                logger.error("[Keychain] Legacy save failed | account=\(key.rawValue, privacy: .public) status=\(legacyStatus)")
            }
        }

        if shouldMirrorToSharedDefaults(key: key) {
            mirrorTokenToSharedDefaults(token, key: key)
        } else {
            clearMirroredTokenFromSharedDefaults(account: key.rawValue)
        }
    }

    /// Retrieve a token from the keychain
    func getToken(key: KeychainKey) -> String? {
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

        if let mirroredToken = mirroredTokenFromSharedDefaults(key: key) {
            if configuredAccessGroup != nil {
                saveToken(mirroredToken, key: key)
            }
            return mirroredToken
        }

        return nil
    }

    /// Delete a specific token from the keychain
    func deleteToken(key: KeychainKey) {
        deleteToken(account: key.rawValue)
    }

    /// Delete a legacy token entry by account name.
    func deleteLegacyToken(named account: String) {
        deleteToken(account: account)
    }

    private func deleteToken(account: String) {
        if let accessGroup = currentAccessGroup() {
            deleteToken(account: account, accessGroup: accessGroup)
        }
        deleteToken(account: account, accessGroup: nil)
        clearMirroredTokenFromSharedDefaults(account: account)
    }

    private func currentAccessGroup() -> String? {
        accessGroupLock.lock()
        defer { accessGroupLock.unlock() }
        return accessGroup
    }

    private func mirrorTokenToSharedDefaults(_ token: String, key: KeychainKey) {
        SharedContainer.userDefaults.set(token, forKey: key.rawValue)
        SharedContainer.userDefaults.synchronize()
    }

    private func mirroredTokenFromSharedDefaults(key: KeychainKey) -> String? {
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
        var query: [String: Any] = baseQuery(account: account, accessGroup: accessGroup)
        query[kSecValueData as String] = data
        // Allow background refreshes after first unlock so timers/URLSession tasks can read tokens
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock

        SecItemDelete(baseQuery(account: account, accessGroup: accessGroup) as CFDictionary)
        return SecItemAdd(query as CFDictionary, nil)
    }

    private func queryToken(account: String, accessGroup: String?) -> String? {
        var query = baseQuery(account: account, accessGroup: accessGroup)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess,
              let data = result as? Data,
              let token = String(data: data, encoding: .utf8) else {
            return nil
        }

        return token
    }

    private func deleteToken(account: String, accessGroup: String?) {
        SecItemDelete(baseQuery(account: account, accessGroup: accessGroup) as CFDictionary)
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
        deleteLegacyToken(named: "openaiApiKey")
    }
}

protocol AuthTokenStore: AnyObject {
    func getToken(key: KeychainManager.KeychainKey) -> String?
    func saveToken(_ token: String, key: KeychainManager.KeychainKey)
    func deleteToken(key: KeychainManager.KeychainKey)
    func clearAll()
}
