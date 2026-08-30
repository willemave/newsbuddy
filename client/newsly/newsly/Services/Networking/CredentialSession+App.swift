import Foundation

extension CredentialSession {
    static var shared: CredentialSession {
        AppCredentialSession.shared
    }
}

private enum AppCredentialSession {
    static let shared: CredentialSession = {
        let tokenStore = KeychainManager.shared
        return CredentialSession(
            storage: CredentialStorageFactory.make(tokenStore: tokenStore),
            exchange: RefreshTokenExchange(
                transport: HTTPTransport(session: .newslyDefault),
                baseURLProvider: { URL(string: AppSettings.shared.baseURL) }
            ),
            processLock: .shared,
            attemptStore: KeychainRefreshAttemptStore(persistence: tokenStore)
        )
    }()
}
