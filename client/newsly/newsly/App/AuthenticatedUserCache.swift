import Foundation

protocol AuthenticatedUserCaching: AnyObject {
    func loadConfirmed() -> User?
    func load(userID: Int) -> User?
    func save(_ user: User)
    func clear()
}

/// Stores the last validated identity in Keychain so process-reclaimed launch
/// can paint the matching user shell while the server session is revalidated.
/// The user-id check prevents a cached profile from crossing account identity.
final class KeychainAuthenticatedUserCache: AuthenticatedUserCaching {
    private let tokenStore: any AuthTokenStore
    private let credentialStorage: any CredentialMaterialStoring
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(
        tokenStore: any AuthTokenStore,
        encoder: JSONEncoder = JSONEncoder(),
        decoder: JSONDecoder = JSONDecoder()
    ) {
        self.tokenStore = tokenStore
        credentialStorage = CredentialStorageFactory.make(tokenStore: tokenStore)
        self.encoder = encoder
        self.decoder = decoder
    }

    func loadConfirmed() -> User? {
        guard let userID = credentialStorage.confirmedUserID() else { return nil }
        return decodeCachedUser(userID: userID)
    }

    func load(userID: Int) -> User? {
        guard credentialStorage.confirmedUserID() == userID else { return nil }
        return decodeCachedUser(userID: userID)
    }

    private func decodeCachedUser(userID: Int) -> User? {
        guard let encoded = tokenStore.getToken(key: .cachedUser),
              let data = Data(base64Encoded: encoded),
              let user = try? decoder.decode(User.self, from: data),
              user.id == userID else {
            return nil
        }
        return user
    }

    func save(_ user: User) {
        guard let data = try? encoder.encode(user) else { return }
        tokenStore.saveToken(String(user.id), key: .userId)
        tokenStore.saveToken(data.base64EncodedString(), key: .cachedUser)
    }

    func clear() {
        tokenStore.deleteToken(key: .cachedUser)
    }
}
