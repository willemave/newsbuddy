import Foundation

extension APIClient {
    static var shared: APIClient {
        AppAPIClient.shared
    }

    convenience init(
        session: URLSession = .newslyDefault,
        decoder: JSONDecoder = JSONDecoder(),
        credentialSession: any CredentialSessionProviding = CredentialSession.shared
    ) {
        self.init(
            session: session,
            baseURLProvider: { URL(string: AppSettings.shared.baseURL) },
            decoder: decoder,
            credentialSession: credentialSession
        )
    }
}

private enum AppAPIClient {
    static let shared = APIClient(
        session: .newslyDefault,
        baseURLProvider: { URL(string: AppSettings.shared.baseURL) },
        credentialSession: CredentialSession.shared
    )
}
