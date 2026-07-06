//
//  TokenRefreshService.swift
//  newsly
//

import Foundation
import os.log

protocol TokenRefreshing: AnyObject {
    var hasStoredCredentialMaterial: Bool { get }

    func accessToken() async throws -> String
    func refreshAccessToken() async throws -> String
}

final class TokenRefreshService: TokenRefreshing {
    static let shared = TokenRefreshService()

    private let session: URLSession
    private let tokenStore: AuthTokenStore
    private let refreshCoordinator = RefreshCoordinator(cooldownSeconds: 10)
    private let logger = Logger(subsystem: "com.newsly", category: "TokenRefreshService")

    init(
        session: URLSession = .newslyDefault,
        tokenStore: AuthTokenStore = KeychainManager.shared
    ) {
        self.session = session
        self.tokenStore = tokenStore
    }

    var hasStoredCredentialMaterial: Bool {
        let accessToken = tokenStore.getToken(key: .accessToken)
        let refreshToken = tokenStore.getToken(key: .refreshToken)
        return !(accessToken?.isEmpty ?? true) || !(refreshToken?.isEmpty ?? true)
    }

    func accessToken() async throws -> String {
        if let token = tokenStore.getToken(key: .accessToken),
           !token.isEmpty {
            return token
        }

        return try await refreshAccessToken()
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
