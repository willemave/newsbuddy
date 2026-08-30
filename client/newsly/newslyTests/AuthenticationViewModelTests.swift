import Foundation
import XCTest
@testable import newsly

@MainActor
final class AuthenticationViewModelTests: XCTestCase {
    func testCachedIdentityPublishesBeforeRemoteValidationCompletes() async {
        let cachedUser = makeUser(fullName: "Cached")
        let tokenStore = AuthenticationTestTokenStore(
            accessToken: "access",
            refreshToken: "refresh",
            userID: cachedUser.id
        )
        let cache = AuthenticationTestUserCache(user: cachedUser)
        let gate = AuthenticationTestGate()
        let service = AuthenticationTestService(
            currentUserResult: .success(makeUser(fullName: "Validated")),
            currentUserGate: gate
        )

        let viewModel = AuthenticationViewModel(
            authService: service,
            tokenStore: tokenStore,
            userCache: cache
        )

        XCTAssertEqual(viewModel.authState, .authenticated(cachedUser))

        await gate.open()
        await waitUntil {
            viewModel.authState == .authenticated(self.makeUser(fullName: "Validated"))
        }
    }

    func testTransientValidationFailureRetainsMatchingCachedIdentity() async {
        let cachedUser = makeUser(fullName: "Cached")
        let tokenStore = AuthenticationTestTokenStore(
            accessToken: "access",
            refreshToken: "refresh",
            userID: cachedUser.id
        )
        let cache = AuthenticationTestUserCache(user: cachedUser)
        let service = AuthenticationTestService(
            currentUserResult: .failure(
                AuthError.networkError(URLError(.notConnectedToInternet))
            )
        )
        let viewModel = AuthenticationViewModel(
            authService: service,
            tokenStore: tokenStore,
            userCache: cache
        )

        await waitUntil { viewModel.errorMessage != nil }

        XCTAssertEqual(viewModel.authState, .authenticated(cachedUser))
        XCTAssertEqual(service.logoutCount, 0)
        XCTAssertEqual(cache.clearCount, 0)
    }

    func testTransientValidationFailureWithoutCachedIdentityStaysRestoring() async {
        let tokenStore = AuthenticationTestTokenStore(
            accessToken: "access",
            refreshToken: "refresh",
            userID: nil
        )
        let cache = AuthenticationTestUserCache(user: nil)
        let service = AuthenticationTestService(
            currentUserResult: .failure(
                AuthError.networkError(URLError(.notConnectedToInternet))
            )
        )
        let viewModel = AuthenticationViewModel(
            authService: service,
            tokenStore: tokenStore,
            userCache: cache
        )

        await waitUntil { viewModel.errorMessage != nil }

        XCTAssertEqual(viewModel.authState, .loading)
        XCTAssertEqual(service.logoutCount, 0)
        XCTAssertEqual(cache.clearCount, 0)
    }

    func testUnavailableCredentialStorageDoesNotClearOrValidateSession() {
        let tokenStore = AuthenticationTestTokenStore(
            accessToken: nil,
            refreshToken: nil,
            userID: nil
        )
        let cache = AuthenticationTestUserCache(user: nil)
        let service = AuthenticationTestService(
            currentUserResult: .failure(AuthError.notAuthenticated)
        )

        let viewModel = AuthenticationViewModel(
            authService: service,
            tokenStore: tokenStore,
            userCache: cache,
            credentialStorage: AuthenticationUnavailableCredentialStorage()
        )

        XCTAssertEqual(viewModel.authState, .loading)
        XCTAssertNotNil(viewModel.errorMessage)
        XCTAssertEqual(service.currentUserCount, 0)
        XCTAssertEqual(service.logoutCount, 0)
        XCTAssertEqual(cache.clearCount, 0)
    }

    func testTerminalAuthenticationRejectionClearsCachedIdentity() async {
        let cachedUser = makeUser(fullName: "Cached")
        let tokenStore = AuthenticationTestTokenStore(
            accessToken: "stale",
            refreshToken: "expired-refresh",
            userID: cachedUser.id
        )
        let cache = AuthenticationTestUserCache(user: cachedUser)
        let service = AuthenticationTestService(
            currentUserResult: .failure(ClientFailure.authenticationExpired)
        )
        let viewModel = AuthenticationViewModel(
            authService: service,
            tokenStore: tokenStore,
            userCache: cache
        )

        await waitUntil { viewModel.authState == .unauthenticated }
        await waitUntil { service.logoutCount == 1 }

        XCTAssertEqual(service.logoutCount, 1)
        XCTAssertEqual(cache.clearCount, 1)
    }

    func testSuccessfulValidationReplacesCachedIdentity() async {
        let cachedUser = makeUser(fullName: "Cached")
        let validatedUser = makeUser(fullName: "Validated")
        let tokenStore = AuthenticationTestTokenStore(
            accessToken: "access",
            refreshToken: "refresh",
            userID: cachedUser.id
        )
        let cache = AuthenticationTestUserCache(user: cachedUser)
        let service = AuthenticationTestService(currentUserResult: .success(validatedUser))
        let viewModel = AuthenticationViewModel(
            authService: service,
            tokenStore: tokenStore,
            userCache: cache
        )

        await waitUntil { viewModel.authState == .authenticated(validatedUser) }

        XCTAssertEqual(cache.savedUsers, [validatedUser])
    }

    func testStaleCurrentUserSuccessCannotRestoreAfterLogout() async {
        let cachedUser = makeUser(fullName: "Cached")
        let staleUser = makeUser(fullName: "Stale validation")
        let gate = AuthenticationTestGate()
        let tokenStore = AuthenticationTestTokenStore(
            accessToken: "access",
            refreshToken: "refresh",
            userID: cachedUser.id
        )
        let cache = AuthenticationTestUserCache(user: cachedUser)
        let service = AuthenticationTestService(
            currentUserResult: .success(staleUser),
            currentUserGate: gate
        )
        let viewModel = AuthenticationViewModel(
            authService: service,
            tokenStore: tokenStore,
            userCache: cache
        )

        await waitUntil { service.currentUserCount == 1 }
        viewModel.logout()
        XCTAssertEqual(viewModel.authState, .unauthenticated)
        await gate.open()
        await waitUntil { service.logoutCount == 1 }
        await Task.yield()

        XCTAssertEqual(viewModel.authState, .unauthenticated)
        XCTAssertTrue(cache.savedUsers.isEmpty)
    }

    func testExplicitLogoutSurfacesSecureCredentialDeletionFailure() async {
        let user = makeUser(fullName: "Reader")
        let tokenStore = AuthenticationTestTokenStore(
            accessToken: nil,
            refreshToken: nil,
            userID: nil
        )
        let cache = AuthenticationTestUserCache(user: nil)
        let service = AuthenticationTestService(
            currentUserResult: .failure(AuthError.notAuthenticated),
            explicitLogoutResult: false
        )
        let viewModel = AuthenticationViewModel(
            authService: service,
            tokenStore: tokenStore,
            userCache: cache
        )
        viewModel.updateUser(user)

        viewModel.logout()

        await waitUntil { viewModel.errorMessage != nil }
        XCTAssertEqual(viewModel.authState, .unauthenticated)
        XCTAssertEqual(
            viewModel.errorMessage,
            "Secure sign-in cleanup did not finish. Please try signing out again."
        )
    }

    func testStaleCurrentUserSuccessCannotReplaceNewAccountState() async {
        let cachedUser = makeUser(fullName: "Cached")
        let staleUser = makeUser(fullName: "Stale validation")
        let replacementUser = makeUser(fullName: "Replacement", id: 84)
        let gate = AuthenticationTestGate()
        let tokenStore = AuthenticationTestTokenStore(
            accessToken: "access",
            refreshToken: "refresh",
            userID: cachedUser.id
        )
        let cache = AuthenticationTestUserCache(user: cachedUser)
        let service = AuthenticationTestService(
            currentUserResult: .success(staleUser),
            currentUserGate: gate
        )
        let viewModel = AuthenticationViewModel(
            authService: service,
            tokenStore: tokenStore,
            userCache: cache
        )

        await waitUntil { service.currentUserCount == 1 }
        viewModel.updateUser(replacementUser)
        await gate.open()
        await Task.yield()

        XCTAssertEqual(viewModel.authState, .authenticated(replacementUser))
        XCTAssertEqual(cache.savedUsers, [replacementUser])
    }

    func testStaleSignInSuccessCannotReauthenticateAfterLogout() async {
        let signedInUser = makeUser(fullName: "Late sign in")
        let signInGate = AuthenticationTestGate()
        let tokenStore = AuthenticationTestTokenStore(
            accessToken: nil,
            refreshToken: nil,
            userID: nil
        )
        let cache = AuthenticationTestUserCache(user: nil)
        let service = AuthenticationTestService(
            currentUserResult: .failure(AuthError.notAuthenticated),
            signInResult: .success(AuthSession(user: signedInUser, isNewUser: false)),
            signInGate: signInGate
        )
        let viewModel = AuthenticationViewModel(
            authService: service,
            tokenStore: tokenStore,
            userCache: cache
        )

        viewModel.signInWithApple()
        await waitUntil { service.signInCount == 1 }
        viewModel.logout()
        await signInGate.open()
        await waitUntil { service.logoutCount == 1 }
        await Task.yield()

        XCTAssertEqual(viewModel.authState, .unauthenticated)
        XCTAssertTrue(cache.savedUsers.isEmpty)
    }

    func testOldTerminalEventDoesNotLogOutNewCredentialState() async {
        let newUser = makeUser(fullName: "New account", id: 84)
        let tokenStore = AuthenticationTestTokenStore(
            accessToken: nil,
            refreshToken: nil,
            userID: nil
        )
        let cache = AuthenticationTestUserCache(user: nil)
        let service = AuthenticationTestService(
            currentUserResult: .failure(AuthError.notAuthenticated),
            terminalLogoutResult: false
        )
        let viewModel = AuthenticationViewModel(
            authService: service,
            tokenStore: tokenStore,
            userCache: cache
        )
        viewModel.updateUser(newUser)

        viewModel.handleTerminalCredentialEvent(
            CredentialTerminalEvent(generation: "old-generation", userID: 42)
        )
        await waitUntil { service.logoutCount == 1 }

        XCTAssertEqual(viewModel.authState, .authenticated(newUser))
        XCTAssertEqual(cache.clearCount, 1)
        XCTAssertEqual(
            service.logoutEvents,
            [CredentialTerminalEvent(generation: "old-generation", userID: 42)]
                as [CredentialTerminalEvent?]
        )
    }

    func testCurrentTerminalEventStillLogsOutAfterProfileUpdate() async {
        let initialUser = makeUser(fullName: "Initial")
        let updatedUser = makeUser(fullName: "Updated profile")
        let logoutGate = AuthenticationTestGate()
        let tokenStore = AuthenticationTestTokenStore(
            accessToken: nil,
            refreshToken: nil,
            userID: nil
        )
        let cache = AuthenticationTestUserCache(user: nil)
        let service = AuthenticationTestService(
            currentUserResult: .failure(AuthError.notAuthenticated),
            logoutGate: logoutGate,
            terminalLogoutResult: true
        )
        let viewModel = AuthenticationViewModel(
            authService: service,
            tokenStore: tokenStore,
            userCache: cache
        )
        viewModel.updateUser(initialUser)

        viewModel.handleTerminalCredentialEvent(
            CredentialTerminalEvent(generation: "current-generation", userID: initialUser.id)
        )
        await waitUntil { service.logoutCount == 1 }
        viewModel.updateUser(updatedUser)
        await logoutGate.open()
        await waitUntil { viewModel.authState == .unauthenticated }

        XCTAssertEqual(viewModel.authState, .unauthenticated)
        XCTAssertNil(cache.loadConfirmed())
    }

    private func waitUntil(
        attempts: Int = 200,
        _ predicate: @escaping @MainActor () -> Bool
    ) async {
        for _ in 0..<attempts {
            if predicate() { return }
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        XCTFail("Condition was not satisfied before timeout")
    }

    private func makeUser(fullName: String, id: Int = 42) -> User {
        User(
            id: id,
            appleId: "apple-\(id)",
            email: "reader@example.com",
            fullName: fullName,
            twitterUsername: nil,
            hasXBookmarkSync: false,
            isAdmin: false,
            isActive: true,
            hasCompletedOnboarding: true,
            hasCompletedNewUserTutorial: true,
            createdAt: Date(timeIntervalSince1970: 1_700_000_000),
            updatedAt: Date(timeIntervalSince1970: 1_700_000_001)
        )
    }
}

private actor AuthenticationTestGate {
    private var isOpen = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        guard !isOpen else { return }
        await withCheckedContinuation { waiters.append($0) }
    }

    func open() {
        isOpen = true
        let pending = waiters
        waiters.removeAll()
        pending.forEach { $0.resume() }
    }
}

private final class AuthenticationTestService: AuthenticationServicing, @unchecked Sendable {
    private let lock = NSLock()
    private let currentUserResult: Result<User, Error>
    private let currentUserGate: AuthenticationTestGate?
    private let signInResult: Result<AuthSession, Error>
    private let signInGate: AuthenticationTestGate?
    private let logoutGate: AuthenticationTestGate?
    private let explicitLogoutResult: Bool
    private let terminalLogoutResult: Bool
    private var recordedLogoutCount = 0
    private var recordedCurrentUserCount = 0
    private var recordedSignInCount = 0
    private var recordedLogoutEvents: [CredentialTerminalEvent?] = []

    init(
        currentUserResult: Result<User, Error>,
        currentUserGate: AuthenticationTestGate? = nil,
        signInResult: Result<AuthSession, Error> = .failure(AuthError.appleSignInFailed),
        signInGate: AuthenticationTestGate? = nil,
        logoutGate: AuthenticationTestGate? = nil,
        explicitLogoutResult: Bool = true,
        terminalLogoutResult: Bool = true
    ) {
        self.currentUserResult = currentUserResult
        self.currentUserGate = currentUserGate
        self.signInResult = signInResult
        self.signInGate = signInGate
        self.logoutGate = logoutGate
        self.explicitLogoutResult = explicitLogoutResult
        self.terminalLogoutResult = terminalLogoutResult
    }

    var logoutCount: Int { lock.withLock { recordedLogoutCount } }
    var currentUserCount: Int { lock.withLock { recordedCurrentUserCount } }
    var signInCount: Int { lock.withLock { recordedSignInCount } }
    var logoutEvents: [CredentialTerminalEvent?] {
        lock.withLock { recordedLogoutEvents }
    }

    @MainActor
    func signInWithApple() async throws -> AuthSession {
        lock.withLock { recordedSignInCount += 1 }
        if let signInGate {
            await signInGate.wait()
        }
        return try signInResult.get()
    }

    func logout(matching event: CredentialTerminalEvent?) async -> Bool {
        lock.withLock {
            recordedLogoutCount += 1
            recordedLogoutEvents.append(event)
        }
        if let logoutGate {
            await logoutGate.wait()
        }
        return event == nil ? explicitLogoutResult : terminalLogoutResult
    }

    func getCurrentUser() async throws -> User {
        lock.withLock { recordedCurrentUserCount += 1 }
        if let currentUserGate {
            await currentUserGate.wait()
        }
        return try currentUserResult.get()
    }

    #if DEBUG
    @MainActor
    func createDebugSession(
        userId: Int?,
        hasCompletedOnboarding: Bool?,
        hasCompletedNewUserTutorial: Bool?
    ) async throws -> AuthSession {
        throw AuthError.appleSignInFailed
    }
    #endif
}

private final class AuthenticationUnavailableCredentialStorage: CredentialMaterialStoring {
    func readEnvelope() -> CredentialStoreRead<CredentialEnvelope> {
        .unavailable
    }

    func readLegacyMaterial() -> CredentialStoreRead<LegacyCredentialMaterial> {
        .unavailable
    }

    func publishEnvelopeAndLegacy(_ envelope: CredentialEnvelope) throws {
        _ = envelope
        throw CredentialStorageError.unavailable
    }

    func publishLegacy(_ tokens: CredentialTokens) throws {
        _ = tokens
        throw CredentialStorageError.unavailable
    }

    func deleteCredentialMaterial() {}
}

private final class AuthenticationTestTokenStore: AuthTokenStore {
    private let lock = NSLock()
    private var storage: [KeychainManager.KeychainKey: String] = [:]

    init(accessToken: String?, refreshToken: String?, userID: Int?) {
        storage[.accessToken] = accessToken
        storage[.refreshToken] = refreshToken
        storage[.userId] = userID.map(String.init)
    }

    func getToken(key: KeychainManager.KeychainKey) -> String? {
        lock.withLock { storage[key] }
    }

    func saveToken(_ token: String, key: KeychainManager.KeychainKey) {
        lock.withLock { storage[key] = token }
    }

    func deleteToken(key: KeychainManager.KeychainKey) {
        lock.withLock { _ = storage.removeValue(forKey: key) }
    }

    func clearAll() {
        lock.withLock { storage.removeAll() }
    }
}

private final class AuthenticationTestUserCache: AuthenticatedUserCaching {
    private var user: User?
    private(set) var savedUsers: [User] = []
    private(set) var clearCount = 0

    init(user: User?) {
        self.user = user
    }

    func loadConfirmed() -> User? {
        user
    }

    func load(userID: Int) -> User? {
        guard user?.id == userID else { return nil }
        return user
    }

    func save(_ user: User) {
        self.user = user
        savedUsers.append(user)
    }

    func clear() {
        user = nil
        clearCount += 1
    }
}
