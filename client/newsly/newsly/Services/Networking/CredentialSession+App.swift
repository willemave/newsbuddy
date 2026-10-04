import Foundation

extension CredentialSession {
    static func makeAppSession(tokenStore: KeychainManager) -> CredentialSession {
        CredentialSession(
            storage: KeychainCredentialStorage(keychain: tokenStore),
            exchange: RefreshTokenExchange(
                transport: HTTPTransport(session: .newslyDefault),
                baseURLProvider: { URL(string: AppSettings.shared.baseURL) }
            ),
            processLock: .shared,
            attemptStore: KeychainRefreshAttemptStore(persistence: tokenStore)
        )
    }
}
