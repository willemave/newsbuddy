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
    private var accessGroup: String?

    enum KeychainKey: String {
        case accessToken = "accessToken"
        case refreshToken = "refreshToken"
        case userId = "userId"
    }

    /// Optional configuration for shared keychain access (e.g., extensions).
    func configure(accessGroup: String?) {
        self.accessGroup = accessGroup
    }

    private let logger = Logger(subsystem: "com.newsly", category: "KeychainManager")

    /// Save a token to the keychain
    func saveToken(_ token: String, key: KeychainKey) {
        guard let data = token.data(using: .utf8) else { return }

        let primaryStatus = upsertToken(data, account: key.rawValue, accessGroup: accessGroup)
        if primaryStatus != errSecSuccess {
            logger.error("[Keychain] Save failed | account=\(key.rawValue, privacy: .public) status=\(primaryStatus)")
        }

        if accessGroup != nil {
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
        if let accessGroup,
           let token = queryToken(account: key.rawValue, accessGroup: accessGroup) {
            return token
        }

        if let legacyToken = queryToken(account: key.rawValue, accessGroup: nil) {
            if accessGroup != nil {
                saveToken(legacyToken, key: key)
            }
            return legacyToken
        }

        if let mirroredToken = mirroredTokenFromSharedDefaults(key: key) {
            if accessGroup != nil {
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
        if let accessGroup {
            deleteToken(account: account, accessGroup: accessGroup)
        }
        deleteToken(account: account, accessGroup: nil)
        clearMirroredTokenFromSharedDefaults(account: account)
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

protocol TokenRefreshing: AnyObject {
    func refreshAccessToken() async throws -> String
}

enum AuthError: Error, LocalizedError {
    case notAuthenticated
    case noRefreshToken
    case refreshTokenExpired
    case refreshFailed
    case serverError(statusCode: Int, message: String?)
    case networkError(Error)
    case appleSignInFailed

    var errorDescription: String? {
        switch self {
        case .notAuthenticated:
            return "Not authenticated"
        case .noRefreshToken:
            return "No refresh token available"
        case .refreshTokenExpired:
            return "Refresh token expired"
        case .refreshFailed:
            return "Failed to refresh token"
        case .serverError(let statusCode, let message):
            return "Server error \(statusCode): \(message ?? "Unknown")"
        case .networkError(let error):
            return "Network error: \(error.localizedDescription)"
        case .appleSignInFailed:
            return "Apple Sign In failed"
        }
    }

    var userFacingMessage: String {
        switch self {
        case .notAuthenticated, .noRefreshToken, .refreshTokenExpired:
            return "Your session expired. Sign in again to continue."
        case .refreshFailed:
            return "We couldn't restore your session. Please try again."
        case .serverError(let statusCode, let message):
            return Self.sanitizedServerMessage(statusCode: statusCode, message: message)
        case .networkError:
            return "We couldn't reach Newsbuddy. Check your connection and try again."
        case .appleSignInFailed:
            return "Apple Sign In couldn't be completed. Please try again."
        }
    }

    private static func sanitizedServerMessage(statusCode: Int, message: String?) -> String {
        guard let extractedMessage = extractedMessage(from: message) else {
            return fallbackMessage(for: statusCode)
        }

        if looksLikeHTML(extractedMessage) || statusCode >= 500 {
            return fallbackMessage(for: statusCode)
        }

        return extractedMessage
    }

    private static func extractedMessage(from rawMessage: String?) -> String? {
        guard let rawMessage else { return nil }

        let trimmed = rawMessage.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }

        if let jsonMessage = jsonFieldMessage(from: trimmed) {
            return jsonMessage
        }

        return trimmed
    }

    private static func jsonFieldMessage(from rawMessage: String) -> String? {
        guard let data = rawMessage.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            return nil
        }

        for key in ["detail", "message", "error", "error_message"] {
            guard let value = object[key] as? String else { continue }
            let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty {
                return trimmed
            }
        }

        return nil
    }

    private static func fallbackMessage(for statusCode: Int) -> String {
        switch statusCode {
        case 429:
            return "Too many attempts right now. Please try again in a moment."
        case 500...599:
            return "Newsbuddy is temporarily unavailable. Please try again in a moment."
        default:
            return "Something went wrong. Please try again."
        }
    }

    private static func looksLikeHTML(_ message: String) -> Bool {
        let lowercaseMessage = message.lowercased()
        let htmlIndicators = [
            "<!doctype",
            "<html",
            "<head",
            "<body",
            "</html",
            "</body",
            "<title",
            "<meta",
            "<div",
            "<span",
            "text/html",
        ]

        return htmlIndicators.contains { lowercaseMessage.contains($0) }
    }
}

final class TokenRefreshService: TokenRefreshing {
    static let shared = TokenRefreshService()

    private let session: URLSession
    private let tokenStore: AuthTokenStore
    private let refreshCoordinator = RefreshCoordinator(cooldownSeconds: 10)
    private let logger = Logger(subsystem: "com.newsly", category: "TokenRefreshService")

    init(
        session: URLSession = .shared,
        tokenStore: AuthTokenStore = KeychainManager.shared
    ) {
        self.session = session
        self.tokenStore = tokenStore
    }

    func refreshAccessToken() async throws -> String {
        if let task = await refreshCoordinator.activeTask() {
            return try await task.value
        }

        if let cached = await refreshCoordinator.cachedToken(
            accessToken: tokenStore.getToken(key: .accessToken)
        ) {
            return cached
        }

        let task = Task { [weak self] () throws -> String in
            defer {
                Task { [weak self] in
                    await self?.refreshCoordinator.clearTask()
                }
            }
            guard let self else { throw AuthError.refreshFailed }
            let token = try await self.performRefreshAccessToken()
            await self.refreshCoordinator.markSuccess()
            return token
        }

        await refreshCoordinator.setTask(task)
        return try await task.value
    }

    private func performRefreshAccessToken() async throws -> String {
        guard let refreshToken = tokenStore.getToken(key: .refreshToken) else {
            logger.error("[AuthRefresh] Missing refresh token")
            throw AuthError.noRefreshToken
        }

        guard let url = URL(string: "\(AppSettings.shared.baseURL)/auth/refresh") else {
            throw AuthError.serverError(statusCode: -1, message: "Invalid refresh URL")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONEncoder().encode(TokenRefreshRequestPayload(refreshToken: refreshToken))

        do {
            let (data, response) = try await session.data(for: request)

            guard let httpResponse = response as? HTTPURLResponse else {
                throw AuthError.serverError(statusCode: -1, message: "Invalid HTTP response")
            }

            switch httpResponse.statusCode {
            case 200:
                let decoder = JSONDecoder()
                let tokenResponse = try decoder.decode(TokenRefreshResponsePayload.self, from: data)
                tokenStore.saveToken(tokenResponse.accessToken, key: .accessToken)
                tokenStore.saveToken(tokenResponse.refreshToken, key: .refreshToken)
                tokenStore.deleteLegacyTokenIfAvailable(named: "openaiApiKey")
                logger.info("[AuthRefresh] Refresh succeeded")
                return tokenResponse.accessToken

            case 401, 403:
                tokenStore.deleteToken(key: .accessToken)
                tokenStore.deleteToken(key: .refreshToken)
                let detail = String(data: data, encoding: .utf8) ?? "Unknown"
                logger.error(
                    "[AuthRefresh] Invalid refresh token | status=\(httpResponse.statusCode) detail=\(detail, privacy: .public)"
                )
                throw AuthError.refreshTokenExpired

            default:
                let detail = String(data: data, encoding: .utf8)
                logger.error(
                    "[AuthRefresh] Refresh failed | status=\(httpResponse.statusCode) detail=\((detail ?? "n/a"), privacy: .public)"
                )
                throw AuthError.serverError(statusCode: httpResponse.statusCode, message: detail)
            }
        } catch let urlError as URLError {
            logger.error(
                "[AuthRefresh] Network error | code=\(urlError.errorCode) description=\(urlError.localizedDescription, privacy: .public)"
            )
            throw AuthError.networkError(urlError)
        } catch let authError as AuthError {
            throw authError
        } catch {
            logger.error("[AuthRefresh] Unexpected error | description=\(error.localizedDescription, privacy: .public)")
            throw AuthError.refreshFailed
        }
    }
}

private struct TokenRefreshRequestPayload: Codable {
    let refreshToken: String

    enum CodingKeys: String, CodingKey {
        case refreshToken = "refresh_token"
    }
}

private struct TokenRefreshResponsePayload: Codable {
    let accessToken: String
    let refreshToken: String

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
    }
}

private actor RefreshCoordinator {
    private var refreshTask: Task<String, Error>?
    private var lastSuccessfulRefresh: Date?
    private let cooldownSeconds: TimeInterval

    init(cooldownSeconds: TimeInterval) {
        self.cooldownSeconds = cooldownSeconds
    }

    func activeTask() -> Task<String, Error>? {
        refreshTask
    }

    func setTask(_ task: Task<String, Error>) {
        refreshTask = task
    }

    func clearTask() {
        refreshTask = nil
    }

    func markSuccess() {
        lastSuccessfulRefresh = Date()
    }

    func cachedToken(accessToken: String?) -> String? {
        guard let lastSuccessfulRefresh,
              Date().timeIntervalSince(lastSuccessfulRefresh) < cooldownSeconds,
              let token = accessToken,
              !token.isEmpty else {
            return nil
        }
        return token
    }
}

private extension AuthTokenStore {
    func deleteLegacyTokenIfAvailable(named account: String) {
        guard let keychainManager = self as? KeychainManager else {
            return
        }
        keychainManager.deleteLegacyToken(named: account)
    }
}
