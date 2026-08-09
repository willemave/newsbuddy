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
    private let processLock: AuthRefreshProcessLock
    private let refreshCoordinator = RefreshCoordinator(cooldownSeconds: 10)
    private let logger = Logger(subsystem: "com.newsly", category: "TokenRefreshService")

    init(
        session: URLSession = .newslyDefault,
        tokenStore: AuthTokenStore = KeychainManager.shared,
        processLock: AuthRefreshProcessLock = .shared
    ) {
        self.session = session
        self.tokenStore = tokenStore
        self.processLock = processLock
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
        let task = await refreshCoordinator.task(
            accessToken: tokenStore.getToken(key: .accessToken)
        ) { [weak self] in
            guard let self else { throw AuthError.refreshFailed }
            do {
                return try await self.processLock.withLock {
                    // The lock may have been contended by the Share Extension.
                    // Read the refresh token only after acquiring it.
                    try await self.performRefreshAccessToken()
                }
            } catch let error as AuthRefreshProcessLockError {
                self.logger.error(
                    "[AuthRefresh] Cross-process lock failed | description=\(error.localizedDescription, privacy: .public)"
                )
                throw AuthError.refreshFailed
            }
        }
        return try await task.value
    }

    private func performRefreshAccessToken(rotatedRetryCount: Int = 1) async throws -> String {
        guard let refreshToken = tokenStore.getToken(key: .refreshToken) else {
            logger.error("[AuthRefresh] Missing refresh token")
            throw AuthError.noRefreshToken
        }
        let attemptedAccessToken = tokenStore.getToken(key: .accessToken)

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
                // Publish the rotated one-time credential first. A concurrent process
                // can then recognize that its rejected token is stale without deleting it.
                tokenStore.saveToken(tokenResponse.refreshToken, key: .refreshToken)
                tokenStore.saveToken(tokenResponse.accessToken, key: .accessToken)
                tokenStore.deleteLegacyTokenIfAvailable(named: "openaiApiKey")
                logger.info("[AuthRefresh] Refresh succeeded")
                return tokenResponse.accessToken

            case 401, 403:
                let currentRefreshToken = tokenStore.getToken(key: .refreshToken)
                if currentRefreshToken != refreshToken {
                    if let currentAccessToken = tokenStore.getToken(key: .accessToken),
                       !currentAccessToken.isEmpty,
                       currentAccessToken != attemptedAccessToken {
                        logger.info("[AuthRefresh] Reusing tokens rotated by another process")
                        return currentAccessToken
                    }
                    if rotatedRetryCount > 0, currentRefreshToken?.isEmpty == false {
                        logger.info("[AuthRefresh] Retrying with refresh token rotated by another process")
                        return try await performRefreshAccessToken(
                            rotatedRetryCount: rotatedRetryCount - 1
                        )
                    }
                    throw AuthError.refreshTokenExpired
                }

                // AuthenticationService performs the eventual logout cleanup.
                // Never delete here: an older extension version that does not yet
                // participate in the process lock could publish a rotated pair
                // between our comparison and a destructive Keychain operation.
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
    private struct ActiveRefresh {
        let id: UUID
        let task: Task<String, Error>
    }

    private var activeRefresh: ActiveRefresh?
    private var lastSuccessfulRefresh: Date?
    private let cooldownSeconds: TimeInterval

    init(cooldownSeconds: TimeInterval) {
        self.cooldownSeconds = cooldownSeconds
    }

    func task(
        accessToken: String?,
        operation: @escaping @Sendable () async throws -> String
    ) -> Task<String, Error> {
        if let activeRefresh {
            return activeRefresh.task
        }

        if let lastSuccessfulRefresh,
           Date().timeIntervalSince(lastSuccessfulRefresh) < cooldownSeconds,
           let accessToken,
           !accessToken.isEmpty {
            return Task { accessToken }
        }

        let refreshID = UUID()
        let task = Task { try await operation() }
        activeRefresh = ActiveRefresh(id: refreshID, task: task)
        Task { [weak self] in
            let succeeded: Bool
            do {
                _ = try await task.value
                succeeded = true
            } catch {
                succeeded = false
            }
            await self?.finish(refreshID: refreshID, succeeded: succeeded)
        }
        return task
    }

    private func finish(refreshID: UUID, succeeded: Bool) {
        guard activeRefresh?.id == refreshID else { return }
        activeRefresh = nil
        if succeeded {
            lastSuccessfulRefresh = Date()
        }
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
