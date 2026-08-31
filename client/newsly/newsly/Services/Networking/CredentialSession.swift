import CryptoKit
import Foundation

enum RequestAuthentication: Equatable, Sendable {
    case required
    case none
}

enum CredentialSessionError: Error, Equatable {
    case storageUnavailable
}

struct CredentialTerminalEvent: Equatable, Sendable {
    let generation: String
    let userID: Int?
}

protocol CredentialSessionProviding: AnyObject {
    var hasStoredCredentialMaterial: Bool { get }

    func accessToken(for authentication: RequestAuthentication) async throws -> String?
    func refreshAfterRejection(rejectedAccessToken: String?) async throws -> String
}

/// Owns one process's credential access, single-flight rotation, cross-process
/// reconciliation, and terminal-event deduplication.
final class CredentialSession: CredentialSessionProviding {
    private let storage: any CredentialMaterialStoring
    private let exchange: any RefreshTokenExchanging
    private let processLock: AuthRefreshProcessLock
    private let attemptStore: any RefreshAttemptStoring
    private let terminalSink: CredentialTerminalSink
    private let refreshCoordinator: CredentialRefreshCoordinator
    private let terminalGate = CredentialTerminalGate()

    init(
        storage: any CredentialMaterialStoring,
        exchange: any RefreshTokenExchanging,
        processLock: AuthRefreshProcessLock,
        attemptStore: any RefreshAttemptStoring,
        cooldownSeconds: TimeInterval = 10,
        terminalHandler: @escaping @Sendable (CredentialTerminalEvent) -> Void = { _ in }
    ) {
        self.storage = storage
        self.exchange = exchange
        self.processLock = processLock
        self.attemptStore = attemptStore
        terminalSink = CredentialTerminalSink(handler: terminalHandler)
        refreshCoordinator = CredentialRefreshCoordinator(cooldownSeconds: cooldownSeconds)
    }

    /// Installs the process composition root's direct terminal-session handler.
    /// The extension leaves the default no-op handler in place.
    func setTerminalHandler(
        _ handler: @escaping @Sendable (CredentialTerminalEvent) -> Void
    ) {
        terminalSink.replaceHandler(handler)
    }

    var hasStoredCredentialMaterial: Bool {
        switch storage.readPendingPublication() {
        case .value, .unavailable:
            return true
        case .missing:
            break
        }
        switch storage.readLegacyMaterial() {
        case .value(let material):
            return material.hasAnyToken
        case .missing:
            if case .value = storage.readEnvelope() { return true }
            return false
        case .unavailable:
            // Unavailable is intentionally not collapsed into a signed-out fact.
            return true
        }
    }

    func accessToken(for authentication: RequestAuthentication) async throws -> String? {
        guard authentication != .none else { return nil }

        let material = try await withCredentialLock { try self.resolveUnderLock() }
        if let token = material.accessToken, !token.isEmpty {
            return token
        }
        guard material.refreshToken?.isEmpty == false else {
            throw ClientFailure.authenticationRequired
        }
        return try await refresh(rejectedAccessToken: nil)
    }

    func refreshAfterRejection(rejectedAccessToken: String?) async throws -> String {
        try await refresh(rejectedAccessToken: rejectedAccessToken)
    }

    func publishAuthenticated(tokens: CredentialTokens, userID: Int) async throws {
        guard tokens.isComplete else { throw CredentialStorageError.writeFailed }
        try await withCredentialLock {
            _ = try self.resolveUnderLock(repairLegacy: false)
            try self.storage.publishEnvelopeAndLegacy(
                CredentialEnvelope(tokens: tokens, userID: userID)
            )
        }
        await refreshCoordinator.invalidate()
    }

    /// Publishes a complete identity-unconfirmed pair for transitional debug and
    /// mixed-version flows. `/auth/me` must validate and bind the pair before it
    /// can establish a cached authenticated identity.
    func publishLegacyCandidate(tokens: CredentialTokens) async throws {
        guard tokens.isComplete else { throw CredentialStorageError.writeFailed }
        try await withCredentialLock {
            _ = try self.resolveUnderLock(repairLegacy: false)
            try self.storage.publishLegacy(tokens)
        }
        await refreshCoordinator.invalidate()
    }

    func bindCurrentCredentials(to userID: Int) async throws {
        try await withCredentialLock {
            let resolved = try self.resolveUnderLock(repairLegacy: false)
            guard let accessToken = resolved.accessToken,
                  let refreshToken = resolved.refreshToken else {
                throw ClientFailure.authenticationRequired
            }

            let tokens = CredentialTokens(
                accessToken: accessToken,
                refreshToken: refreshToken
            )
            if let envelope = resolved.envelope,
               envelope.userID == userID,
               envelope.tokens == tokens {
                try self.storage.publishEnvelopeAndLegacy(envelope)
                return
            }
            try self.storage.publishEnvelopeAndLegacy(
                CredentialEnvelope(tokens: tokens, userID: userID)
            )
        }
        await refreshCoordinator.invalidate()
    }

    @discardableResult
    func clearCredentials(ifCurrent event: CredentialTerminalEvent? = nil) async throws -> Bool {
        do {
            let didClear = try await withCredentialLock {
                if let event {
                    let current = try self.resolveUnderLock(repairLegacy: false)
                    guard Self.terminalEvent(for: current) == event else {
                        return false
                    }
                }
                try self.storage.deleteCredentialMaterial()
                return true
            }
            if didClear {
                await refreshCoordinator.invalidate()
            }
            return didClear
        } catch {
            // A logout intent must not leave a successful refresh cached in
            // memory even when secure storage could not finish deletion.
            await refreshCoordinator.invalidate()
            throw error
        }
    }

    private func refresh(rejectedAccessToken: String?) async throws -> String {
        // Join same-rejection work before taking the cross-process lock. If key
        // discovery acquired that lock first, queued callers would only reach
        // the coordinator after rotation and incorrectly start another refresh.
        let key = CredentialRefreshKey(
            rejectedAccessTokenFingerprint: rejectedAccessToken.map(Self.tokenFingerprint)
        )
        let task = await refreshCoordinator.task(for: key) { [weak self] in
            guard let self else { throw ClientFailure.unexpected }
            return try await self.performRefresh(rejectedAccessToken: rejectedAccessToken)
        }
        return try await task.value
    }

    private func performRefresh(rejectedAccessToken: String?) async throws -> String {
        do {
            return try await withCredentialLock {
                var resolved = try self.resolveUnderLock()
                var rotatedRetryRemaining = true

                while true {
                    if let rejectedAccessToken,
                       let currentAccessToken = resolved.accessToken,
                       currentAccessToken != rejectedAccessToken {
                        return currentAccessToken
                    }
                    guard let refreshToken = resolved.refreshToken, !refreshToken.isEmpty else {
                        if rejectedAccessToken != nil {
                            await self.emitTerminalOnce(for: resolved)
                        }
                        throw ClientFailure.authenticationRequired
                    }

                    let attemptID: String
                    do {
                        attemptID = try self.attemptStore.attemptID(for: refreshToken)
                    } catch {
                        throw CredentialSessionError.storageUnavailable
                    }
                    do {
                        let tokens = try await self.exchange.exchange(
                            refreshToken: refreshToken,
                            attemptID: attemptID
                        )
                        if let envelope = resolved.envelope,
                           resolved.isEnvelopeConfirmed {
                            try self.storage.publishEnvelopeAndLegacy(
                                CredentialEnvelope(tokens: tokens, userID: envelope.userID)
                            )
                        } else {
                            // Identity is unknown for loose legacy credentials.
                            // Keep the pair usable, then `/auth/me` binds it.
                            try self.storage.publishLegacy(tokens)
                        }
                        self.attemptStore.clearAttempt(for: refreshToken)
                        return tokens.accessToken
                    } catch ClientFailure.authenticationExpired {
                        self.attemptStore.clearAttempt(for: refreshToken)
                        let latest = try self.resolveUnderLock()
                        if latest.refreshToken != refreshToken,
                           latest.refreshToken?.isEmpty == false {
                            if latest.accessToken != resolved.accessToken,
                               let accessToken = latest.accessToken {
                                return accessToken
                            }
                            if rotatedRetryRemaining {
                                rotatedRetryRemaining = false
                                resolved = latest
                                continue
                            }
                        }
                        await self.emitTerminalOnce(for: latest)
                        throw ClientFailure.authenticationExpired
                    }
                }
            }
        } catch is AuthRefreshProcessLockError {
            throw CredentialSessionError.storageUnavailable
        }
    }

    private func resolveUnderLock(repairLegacy: Bool = true) throws -> ResolvedCredentialMaterial {
        switch storage.readPendingPublication() {
        case .value(let publication):
            do {
                try storage.completePendingPublication(publication)
            } catch {
                throw CredentialSessionError.storageUnavailable
            }
        case .missing:
            break
        case .unavailable:
            throw CredentialSessionError.storageUnavailable
        }

        let envelope: CredentialEnvelope?
        switch storage.readEnvelope() {
        case .value(let value):
            envelope = value
        case .missing:
            envelope = nil
        case .unavailable:
            throw CredentialSessionError.storageUnavailable
        }

        let legacy: LegacyCredentialMaterial?
        switch storage.readLegacyMaterial() {
        case .value(let value):
            legacy = value
        case .missing:
            legacy = nil
        case .unavailable:
            throw CredentialSessionError.storageUnavailable
        }

        if let envelope {
            let relationship = legacy?.relationship(to: envelope)
                ?? .repairableIncomplete
            switch relationship {
            case .coherentTakeover:
                return ResolvedCredentialMaterial(
                    accessToken: legacy?.accessToken,
                    refreshToken: legacy?.refreshToken,
                    envelope: envelope,
                    isEnvelopeConfirmed: false
                )
            case .unsafeDivergence:
                // Never overwrite an unmatched leg. old-access/new-refresh can
                // be the residue of a refresh whose old token is already spent.
                throw CredentialSessionError.storageUnavailable
            case .matching, .repairableIncomplete:
                break
            }
            if repairLegacy,
               (relationship == .repairableIncomplete
                   || legacy?.needsReconciliation == true) {
                do {
                    try storage.publishLegacy(envelope.tokens)
                } catch {
                    throw CredentialSessionError.storageUnavailable
                }
            }
            return ResolvedCredentialMaterial(
                accessToken: envelope.tokens.accessToken,
                refreshToken: envelope.tokens.refreshToken,
                envelope: envelope,
                isEnvelopeConfirmed: true
            )
        }

        guard let legacy else {
            return ResolvedCredentialMaterial(
                accessToken: nil,
                refreshToken: nil,
                envelope: nil,
                isEnvelopeConfirmed: false
            )
        }
        return ResolvedCredentialMaterial(
            accessToken: legacy.accessToken,
            refreshToken: legacy.refreshToken,
            envelope: nil,
            isEnvelopeConfirmed: false
        )
    }

    private func emitTerminalOnce(for material: ResolvedCredentialMaterial) async {
        let event = Self.terminalEvent(for: material)
        if await terminalGate.shouldEmit(generation: event.generation) {
            terminalSink.emit(event)
        }
    }

    private static func terminalEvent(
        for material: ResolvedCredentialMaterial
    ) -> CredentialTerminalEvent {
        if let envelope = material.envelope, material.isEnvelopeConfirmed {
            return CredentialTerminalEvent(
                generation: envelope.generation.uuidString.lowercased(),
                userID: envelope.userID
            )
        }
        return CredentialTerminalEvent(
            generation: legacyGeneration(
                accessToken: material.accessToken,
                refreshToken: material.refreshToken
            ),
            userID: nil
        )
    }

    private func withCredentialLock<T>(
        _ operation: @escaping () async throws -> T
    ) async throws -> T {
        try await processLock.withLock(operation)
    }

    private static func legacyGeneration(accessToken: String?, refreshToken: String?) -> String {
        let material = "\(accessToken ?? "")\u{0}\(refreshToken ?? "")"
        return sha256Hex(material)
    }

    private static func tokenFingerprint(_ token: String) -> String {
        sha256Hex(token)
    }

    private static func sha256Hex(_ value: String) -> String {
        SHA256.hash(data: Data(value.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
    }
}

private final class CredentialTerminalSink: @unchecked Sendable {
    private let lock = NSLock()
    private var handler: @Sendable (CredentialTerminalEvent) -> Void

    init(handler: @escaping @Sendable (CredentialTerminalEvent) -> Void) {
        self.handler = handler
    }

    func replaceHandler(
        _ handler: @escaping @Sendable (CredentialTerminalEvent) -> Void
    ) {
        lock.withLock { self.handler = handler }
    }

    func emit(_ event: CredentialTerminalEvent) {
        let currentHandler: @Sendable (CredentialTerminalEvent) -> Void = lock.withLock {
            self.handler
        }
        currentHandler(event)
    }
}

private struct ResolvedCredentialMaterial {
    let accessToken: String?
    let refreshToken: String?
    let envelope: CredentialEnvelope?
    let isEnvelopeConfirmed: Bool
}

private actor CredentialTerminalGate {
    private var emittedGenerations: Set<String> = []

    func shouldEmit(generation: String) -> Bool {
        emittedGenerations.insert(generation).inserted
    }
}

private struct CredentialRefreshKey: Hashable, Sendable {
    let rejectedAccessTokenFingerprint: String?
}

private actor CredentialRefreshCoordinator {
    private struct ActiveRefresh {
        let id: UUID
        let task: Task<String, Error>
    }

    private var epoch: UInt64 = 0
    private var activeRefreshes: [CredentialRefreshKey: ActiveRefresh] = [:]
    private var lastSuccess: (
        key: CredentialRefreshKey,
        date: Date,
        accessToken: String
    )?
    private let cooldownSeconds: TimeInterval

    init(cooldownSeconds: TimeInterval) {
        self.cooldownSeconds = cooldownSeconds
    }

    func task(
        for key: CredentialRefreshKey,
        operation: @escaping @Sendable () async throws -> String
    ) -> Task<String, Error> {
        if let activeRefresh = activeRefreshes[key] {
            return activeRefresh.task
        }
        if let lastSuccess,
           lastSuccess.key == key,
           Date().timeIntervalSince(lastSuccess.date) < cooldownSeconds {
            return Task { lastSuccess.accessToken }
        }

        let refreshID = UUID()
        let refreshEpoch = epoch
        let task = Task { try await operation() }
        activeRefreshes[key] = ActiveRefresh(
            id: refreshID,
            task: task
        )
        Task { [weak self] in
            let accessToken = try? await task.value
            await self?.finish(
                key: key,
                refreshID: refreshID,
                refreshEpoch: refreshEpoch,
                accessToken: accessToken
            )
        }
        return task
    }

    func invalidate() {
        epoch &+= 1
        activeRefreshes.removeAll()
        lastSuccess = nil
    }

    private func finish(
        key: CredentialRefreshKey,
        refreshID: UUID,
        refreshEpoch: UInt64,
        accessToken: String?
    ) {
        guard epoch == refreshEpoch,
              let activeRefresh = activeRefreshes[key],
              activeRefresh.id == refreshID else {
            return
        }
        activeRefreshes.removeValue(forKey: key)
        if let accessToken {
            lastSuccess = (key, Date(), accessToken)
        }
    }
}
