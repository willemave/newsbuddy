import Foundation

extension APIClient {
    convenience init(
        session: URLSession = .newslyDefault,
        decoder: JSONDecoder = JSONDecoder(),
        credentialSession: any CredentialSessionProviding
    ) {
        self.init(
            session: session,
            baseURLProvider: { URL(string: AppSettings.shared.baseURL) },
            decoder: decoder,
            credentialSession: credentialSession
        )
    }
}
