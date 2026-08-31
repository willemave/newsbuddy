import Foundation
import XCTest
@testable import newsly

final class CredentialSessionTests: XCTestCase {
    func testSignInJournalsBeforePublishingRefreshFirstAndEnvelopeLast() async throws {
        let tokenStore = RecordingAuthTokenStore()
        let storage = TokenStoreCredentialStorage(tokenStore: tokenStore)
        let session = makeCredentialSession(storage: storage)

        try await session.publishAuthenticated(
            tokens: CredentialTokens(accessToken: "access", refreshToken: "refresh"),
            userID: 42
        )

        XCTAssertEqual(
            tokenStore.savedKeys,
            [
                .credentialPublication,
                .refreshToken,
                .accessToken,
                .userId,
                .credentialEnvelope,
            ]
        )
        guard case .value(let envelope) = storage.readEnvelope() else {
            return XCTFail("Expected a published envelope")
        }
        XCTAssertEqual(envelope.userID, 42)
        XCTAssertEqual(envelope.tokens.accessToken, "access")
        XCTAssertEqual(envelope.tokens.refreshToken, "refresh")
    }

    func testLegacyCandidatePublishesRefreshFirstAndBindsOnlyAfterValidation() async throws {
        let tokenStore = RecordingAuthTokenStore()
        let storage = TokenStoreCredentialStorage(tokenStore: tokenStore)
        let session = makeCredentialSession(storage: storage)

        try await session.publishLegacyCandidate(
            tokens: CredentialTokens(
                accessToken: "candidate-access",
                refreshToken: "candidate-refresh"
            )
        )

        XCTAssertEqual(tokenStore.savedKeys, [.refreshToken, .accessToken])
        guard case .missing = storage.readEnvelope() else {
            return XCTFail("An unvalidated candidate must not establish identity")
        }

        try await session.bindCurrentCredentials(to: 42)

        XCTAssertEqual(
            tokenStore.savedKeys,
            [
                .refreshToken,
                .accessToken,
                .credentialPublication,
                .refreshToken,
                .accessToken,
                .userId,
                .credentialEnvelope,
            ]
        )
        XCTAssertEqual(storage.confirmedUserID(), 42)
    }

    func testConfirmedIdentityRequiresEnvelopeAndMatchingLegacyPair() {
        let envelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "access", refreshToken: "refresh"),
            userID: 42
        )
        let storage = RecordingCredentialStorage(
            envelope: envelope,
            legacy: LegacyCredentialMaterial(
                accessToken: "access",
                refreshToken: "refresh"
            )
        )
        XCTAssertEqual(storage.confirmedUserID(), 42)

        storage.legacy = LegacyCredentialMaterial(
            accessToken: "other-access",
            refreshToken: "other-refresh"
        )
        XCTAssertNil(storage.confirmedUserID())
    }

    func testCachedUserLoadKeepsEnvelopeWhenOnlyOneLegacyLegChanged() throws {
        let tokenStore = RecordingAuthTokenStore()
        let storage = TokenStoreCredentialStorage(tokenStore: tokenStore)
        let user = credentialUser()
        try storage.publishEnvelopeAndLegacy(
            CredentialEnvelope(
                tokens: CredentialTokens(accessToken: "access", refreshToken: "refresh"),
                userID: user.id
            )
        )
        let cache = KeychainAuthenticatedUserCache(tokenStore: tokenStore)
        cache.save(user)

        XCTAssertEqual(cache.loadConfirmed(), user)

        tokenStore.saveToken("other-access", key: .accessToken)
        XCTAssertEqual(cache.loadConfirmed(), user)
    }

    func testEnvelopeRepairsOnlyNondivergentIncompleteLegacyMaterial() async throws {
        let envelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "envelope-access", refreshToken: "envelope-refresh"),
            userID: 42
        )
        let repairableLegacyValues = [
            LegacyCredentialMaterial(
                accessToken: "envelope-access",
                refreshToken: nil
            ),
            LegacyCredentialMaterial(
                accessToken: nil,
                refreshToken: "envelope-refresh"
            ),
        ]

        for legacy in repairableLegacyValues {
            let storage = RecordingCredentialStorage(envelope: envelope, legacy: legacy)
            let session = makeCredentialSession(storage: storage)

            let accessToken = try await session.accessToken(for: .required)

            XCTAssertEqual(accessToken, "envelope-access")
            XCTAssertEqual(storage.legacy?.completeTokens, envelope.tokens)
            XCTAssertEqual(storage.confirmedUserID(), 42)
        }
    }

    func testUnjournaledInterruptedRefreshPreservesNewRefreshAndFailsUnavailable() async {
        let envelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "old-access", refreshToken: "old-refresh"),
            userID: 42
        )
        let interruptedRefresh = LegacyCredentialMaterial(
            accessToken: "old-access",
            refreshToken: "new-refresh"
        )
        let storage = RecordingCredentialStorage(
            envelope: envelope,
            legacy: interruptedRefresh
        )
        let session = makeCredentialSession(storage: storage)

        do {
            _ = try await session.accessToken(for: .required)
            XCTFail("Expected unsafe interrupted refresh state")
        } catch {
            XCTAssertEqual(error as? CredentialSessionError, .storageUnavailable)
        }
        XCTAssertEqual(storage.envelope, envelope)
        XCTAssertEqual(storage.legacy, interruptedRefresh)
        XCTAssertEqual(storage.legacy?.refreshToken, "new-refresh")
        XCTAssertEqual(storage.confirmedUserID(), 42)
    }

    func testPendingPublicationRecoversEveryInterruptedWriteBoundary() async throws {
        let oldEnvelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "old-access", refreshToken: "old-refresh"),
            userID: 42
        )
        let targetEnvelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "new-access", refreshToken: "new-refresh"),
            userID: 42
        )
        let publication = CredentialPublication(
            target: targetEnvelope,
            baselineEnvelope: oldEnvelope,
            baselineAccessToken: "old-access",
            baselineRefreshToken: "old-refresh",
            baselineUserID: "42"
        )
        let boundaries: [(CredentialEnvelope, LegacyCredentialMaterial)] = [
            (
                oldEnvelope,
                LegacyCredentialMaterial(
                    accessToken: "old-access",
                    refreshToken: "old-refresh"
                )
            ),
            (
                oldEnvelope,
                LegacyCredentialMaterial(
                    accessToken: "old-access",
                    refreshToken: "new-refresh"
                )
            ),
            (
                oldEnvelope,
                LegacyCredentialMaterial(
                    accessToken: "new-access",
                    refreshToken: "new-refresh"
                )
            ),
            (
                targetEnvelope,
                LegacyCredentialMaterial(
                    accessToken: "new-access",
                    refreshToken: "new-refresh"
                )
            ),
        ]

        for (envelope, legacy) in boundaries {
            let storage = RecordingCredentialStorage(
                envelope: envelope,
                legacy: legacy,
                pendingPublication: publication
            )
            let session = makeCredentialSession(storage: storage)

            XCTAssertNil(storage.confirmedUserID())
            let accessToken = try await session.accessToken(for: .required)

            XCTAssertEqual(accessToken, "new-access")
            XCTAssertEqual(storage.envelope, targetEnvelope)
            XCTAssertEqual(storage.legacy?.completeTokens, targetEnvelope.tokens)
            XCTAssertEqual(storage.confirmedUserID(), 42)
            guard case .missing = storage.readPendingPublication() else {
                return XCTFail("Expected the recovered publication journal to be removed")
            }
        }
    }

    func testTokenStoreAdapterRecoversJournaledPartialRefresh() async throws {
        let tokenStore = RecordingAuthTokenStore()
        let oldEnvelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "old-access", refreshToken: "old-refresh"),
            userID: 42
        )
        let targetEnvelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "new-access", refreshToken: "new-refresh"),
            userID: 42
        )
        let publication = CredentialPublication(
            target: targetEnvelope,
            baselineEnvelope: oldEnvelope,
            baselineAccessToken: "old-access",
            baselineRefreshToken: "old-refresh",
            baselineUserID: "42"
        )
        tokenStore.saveToken(
            try JSONEncoder().encode(oldEnvelope).base64EncodedString(),
            key: .credentialEnvelope
        )
        tokenStore.saveToken("old-access", key: .accessToken)
        tokenStore.saveToken("new-refresh", key: .refreshToken)
        tokenStore.saveToken("42", key: .userId)
        tokenStore.saveToken(
            try JSONEncoder().encode(publication).base64EncodedString(),
            key: .credentialPublication
        )
        let storage = TokenStoreCredentialStorage(tokenStore: tokenStore)
        let session = makeCredentialSession(storage: storage)

        let accessToken = try await session.accessToken(for: .required)

        XCTAssertEqual(accessToken, "new-access")
        guard case .value(let recoveredEnvelope) = storage.readEnvelope() else {
            return XCTFail("Expected the target envelope after journal recovery")
        }
        XCTAssertEqual(recoveredEnvelope, targetEnvelope)
        guard case .missing = storage.readPendingPublication() else {
            return XCTFail("Expected the publication journal to be removed")
        }
    }

    func testStalePublicationDefersToUnrelatedCommittedCredentialState() async throws {
        let oldEnvelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "old-access", refreshToken: "old-refresh"),
            userID: 42
        )
        let targetEnvelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "target-access", refreshToken: "target-refresh"),
            userID: 42
        )
        let newerEnvelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "newer-access", refreshToken: "newer-refresh"),
            userID: 84
        )
        let publication = CredentialPublication(
            target: targetEnvelope,
            baselineEnvelope: oldEnvelope,
            baselineAccessToken: "old-access",
            baselineRefreshToken: "old-refresh",
            baselineUserID: "42"
        )
        let storage = RecordingCredentialStorage(
            envelope: newerEnvelope,
            legacy: LegacyCredentialMaterial(
                accessToken: "newer-access",
                refreshToken: "newer-refresh"
            ),
            pendingPublication: publication
        )
        let session = makeCredentialSession(storage: storage)

        let accessToken = try await session.accessToken(for: .required)

        XCTAssertEqual(accessToken, "newer-access")
        XCTAssertEqual(storage.envelope, newerEnvelope)
        XCTAssertEqual(storage.legacy?.completeTokens, newerEnvelope.tokens)
        guard case .missing = storage.readPendingPublication() else {
            return XCTFail("Expected the superseded publication to be discarded")
        }
    }

    func testPendingPublicationDoesNotResurrectDeletedCredentials() async {
        let oldEnvelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "old-access", refreshToken: "old-refresh"),
            userID: 42
        )
        let publication = CredentialPublication(
            target: CredentialEnvelope(
                tokens: CredentialTokens(
                    accessToken: "target-access",
                    refreshToken: "target-refresh"
                ),
                userID: 42
            ),
            baselineEnvelope: oldEnvelope,
            baselineAccessToken: "old-access",
            baselineRefreshToken: "old-refresh",
            baselineUserID: "42"
        )
        let storage = RecordingCredentialStorage(pendingPublication: publication)
        let session = makeCredentialSession(storage: storage)

        do {
            _ = try await session.accessToken(for: .required)
            XCTFail("Expected deleted credentials to remain signed out")
        } catch {
            XCTAssertEqual(ClientFailure.classify(error), .authenticationRequired)
        }
        XCTAssertNil(storage.envelope)
        XCTAssertNil(storage.legacy)
        guard case .missing = storage.readPendingPublication() else {
            return XCTFail("Expected the stale publication to be discarded")
        }
    }

    func testInterruptedSignInAccessLegCannotTakeOverEnvelope() async {
        let envelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "old-access", refreshToken: "old-refresh"),
            userID: 42
        )
        let interruptedSignIn = LegacyCredentialMaterial(
            accessToken: "new-access",
            refreshToken: "old-refresh"
        )
        let storage = RecordingCredentialStorage(
            envelope: envelope,
            legacy: interruptedSignIn
        )
        let session = makeCredentialSession(storage: storage)

        do {
            _ = try await session.accessToken(for: .required)
            XCTFail("Expected unsafe interrupted sign-in state")
        } catch {
            XCTAssertEqual(error as? CredentialSessionError, .storageUnavailable)
        }
        XCTAssertEqual(storage.envelope, envelope)
        XCTAssertEqual(storage.legacy, interruptedSignIn)
        XCTAssertEqual(storage.confirmedUserID(), 42)
    }

    func testCrossSourceCompletePairCannotTakeOverEnvelope() async {
        let envelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "old-access", refreshToken: "old-refresh"),
            userID: 42
        )
        let crossSourcePair = LegacyCredentialMaterial(
            accessToken: "other-access",
            refreshToken: "other-refresh",
            isCoherentPair: false
        )
        let storage = RecordingCredentialStorage(
            envelope: envelope,
            legacy: crossSourcePair
        )
        let session = makeCredentialSession(storage: storage)

        do {
            _ = try await session.accessToken(for: .required)
            XCTFail("Expected cross-source pair rejection")
        } catch {
            XCTAssertEqual(error as? CredentialSessionError, .storageUnavailable)
        }
        XCTAssertEqual(storage.envelope, envelope)
        XCTAssertEqual(storage.legacy, crossSourcePair)
    }

    func testFailedEnvelopeCompatibilityRepairFailsUnavailableWithoutDemotion() async {
        let envelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "envelope-access", refreshToken: "envelope-refresh"),
            userID: 42
        )
        let partialLegacy = LegacyCredentialMaterial(
            accessToken: "envelope-access",
            refreshToken: nil
        )
        let storage = RecordingCredentialStorage(
            envelope: envelope,
            legacy: partialLegacy,
            publishLegacyFails: true
        )
        let session = makeCredentialSession(storage: storage)

        do {
            _ = try await session.accessToken(for: .required)
            XCTFail("Expected compatibility repair failure")
        } catch {
            XCTAssertEqual(error as? CredentialSessionError, .storageUnavailable)
        }
        XCTAssertEqual(storage.envelope, envelope)
        XCTAssertEqual(storage.legacy, partialLegacy)
    }

    func testLegacyPairCannotEstablishCachedIdentityUntilServerBinding() async throws {
        let storage = RecordingCredentialStorage(
            legacy: LegacyCredentialMaterial(
                accessToken: "legacy-access",
                refreshToken: "legacy-refresh"
            )
        )
        let session = makeCredentialSession(storage: storage)

        XCTAssertNil(storage.confirmedUserID())
        try await session.bindCurrentCredentials(to: 84)

        XCTAssertEqual(storage.confirmedUserID(), 84)
        XCTAssertEqual(storage.envelope?.tokens.accessToken, "legacy-access")
    }

    func testLegacyRotationWinsUntilValidatedAndPromoted() async throws {
        let oldEnvelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "old-access", refreshToken: "old-refresh"),
            userID: 42
        )
        let storage = RecordingCredentialStorage(
            envelope: oldEnvelope,
            legacy: LegacyCredentialMaterial(
                accessToken: "extension-access",
                refreshToken: "extension-refresh"
            )
        )
        let session = makeCredentialSession(storage: storage)

        let candidate = try await session.accessToken(for: .required)
        XCTAssertEqual(candidate, "extension-access")
        XCTAssertNil(storage.confirmedUserID())

        try await session.bindCurrentCredentials(to: 42)
        XCTAssertEqual(storage.confirmedUserID(), 42)
        XCTAssertEqual(storage.envelope?.tokens.refreshToken, "extension-refresh")
        XCTAssertNotEqual(storage.envelope?.generation, oldEnvelope.generation)
    }

    func testEnvelopeSuppressesStalePlaintextMirrorFallback() {
        XCTAssertEqual(
            CredentialFallbackPolicy.token(
                envelope: "secure-current",
                plaintextMirror: "plaintext-stale"
            ),
            "secure-current"
        )
        XCTAssertEqual(
            CredentialFallbackPolicy.token(
                envelope: nil,
                plaintextMirror: "legacy-only"
            ),
            "legacy-only"
        )
    }

    func testMissingCredentialsAndUnavailableStorageRemainDistinct() async {
        let missing = makeCredentialSession(storage: RecordingCredentialStorage())
        do {
            _ = try await missing.accessToken(for: .required)
            XCTFail("Expected missing credentials")
        } catch {
            XCTAssertEqual(ClientFailure.classify(error), .authenticationRequired)
        }

        let unavailable = makeCredentialSession(
            storage: RecordingCredentialStorage(isUnavailable: true)
        )
        do {
            _ = try await unavailable.accessToken(for: .required)
            XCTFail("Expected unavailable storage")
        } catch {
            XCTAssertEqual(error as? CredentialSessionError, .storageUnavailable)
        }
    }

    func testRefreshAttemptWriteFailureFailsClosedBeforeNetworkExchange() async {
        let storage = RecordingCredentialStorage(
            legacy: LegacyCredentialMaterial(
                accessToken: "rejected-access",
                refreshToken: "refresh-token"
            )
        )
        let persistence = RecordingRefreshAttemptPersistence(writeFails: true)
        let exchange = RecordingRefreshExchange { _, _ in
            XCTFail("Refresh exchange must not run without a durable attempt ID")
            return CredentialTokens(accessToken: "unused", refreshToken: "unused")
        }
        let session = makeCredentialSession(
            storage: storage,
            exchange: exchange,
            attemptStore: KeychainRefreshAttemptStore(persistence: persistence)
        )

        do {
            _ = try await session.refreshAfterRejection(
                rejectedAccessToken: "rejected-access"
            )
            XCTFail("Expected attempt persistence failure")
        } catch {
            XCTAssertEqual(error as? CredentialSessionError, .storageUnavailable)
        }

        XCTAssertEqual(persistence.writeCount, 1)
        XCTAssertEqual(exchange.callCount, 0)
    }

    func testRefreshAttemptIDRemainsStableAcrossStoreRecreation() throws {
        let persistence = RecordingRefreshAttemptPersistence()
        let firstStore = KeychainRefreshAttemptStore(persistence: persistence)

        let firstAttempt = try firstStore.attemptID(for: "refresh-token")
        let repeatedAttempt = try firstStore.attemptID(for: "refresh-token")
        let relaunchedStore = KeychainRefreshAttemptStore(persistence: persistence)
        let relaunchedAttempt = try relaunchedStore.attemptID(for: "refresh-token")

        XCTAssertEqual(repeatedAttempt, firstAttempt)
        XCTAssertEqual(relaunchedAttempt, firstAttempt)
        XCTAssertEqual(persistence.writeCount, 1)
    }

    func testConcurrentBearerFailuresShareExchangeAndEmitOneTerminalEvent() async {
        let storage = RecordingCredentialStorage(
            legacy: LegacyCredentialMaterial(
                accessToken: "rejected-access",
                refreshToken: "rejected-refresh"
            )
        )
        let gate = CredentialTestGate()
        let exchangeStarted = expectation(description: "refresh exchange started")
        exchangeStarted.assertForOverFulfill = false
        let exchange = RecordingRefreshExchange { _, _ in
            exchangeStarted.fulfill()
            await gate.wait()
            throw ClientFailure.authenticationExpired
        }
        let terminalEvents = CredentialLockedValues<CredentialTerminalEvent>()
        let session = makeCredentialSession(
            storage: storage,
            exchange: exchange,
            terminalHandler: { terminalEvents.append($0) }
        )

        let tasks = (0..<12).map { _ in
            Task {
                try await session.refreshAfterRejection(
                    rejectedAccessToken: "rejected-access"
                )
            }
        }
        await fulfillment(of: [exchangeStarted], timeout: 1)
        await gate.open()

        for task in tasks {
            do {
                _ = try await task.value
                XCTFail("Expected terminal rejection")
            } catch {
                XCTAssertEqual(ClientFailure.classify(error), .authenticationExpired)
            }
        }

        XCTAssertEqual(exchange.callCount, 1)
        XCTAssertEqual(terminalEvents.values.count, 1)
    }

    func testRejectedAccessWithoutRefreshEmitsOneTerminalEvent() async {
        let storage = RecordingCredentialStorage(
            legacy: LegacyCredentialMaterial(
                accessToken: "rejected-access",
                refreshToken: nil
            )
        )
        let terminalEvents = CredentialLockedValues<CredentialTerminalEvent>()
        let session = makeCredentialSession(
            storage: storage,
            terminalHandler: { terminalEvents.append($0) }
        )

        for _ in 0..<2 {
            do {
                _ = try await session.refreshAfterRejection(
                    rejectedAccessToken: "rejected-access"
                )
                XCTFail("Expected missing refresh credential")
            } catch {
                XCTAssertEqual(ClientFailure.classify(error), .authenticationRequired)
            }
        }

        XCTAssertEqual(terminalEvents.values.count, 1)
    }

    func testRefreshPublishesNewEnvelopeForSameIdentity() async throws {
        let oldEnvelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "old-access", refreshToken: "old-refresh"),
            userID: 42
        )
        let storage = RecordingCredentialStorage(
            envelope: oldEnvelope,
            legacy: LegacyCredentialMaterial(
                accessToken: "old-access",
                refreshToken: "old-refresh"
            )
        )
        let exchange = RecordingRefreshExchange { token, attemptID in
            XCTAssertEqual(token, "old-refresh")
            XCTAssertEqual(attemptID, "attempt-old-refresh")
            return CredentialTokens(accessToken: "new-access", refreshToken: "new-refresh")
        }
        let session = makeCredentialSession(storage: storage, exchange: exchange)

        let accessToken = try await session.refreshAfterRejection(
            rejectedAccessToken: "old-access"
        )

        XCTAssertEqual(accessToken, "new-access")
        XCTAssertEqual(storage.envelope?.userID, 42)
        XCTAssertEqual(storage.envelope?.tokens.refreshToken, "new-refresh")
        XCTAssertNotEqual(storage.envelope?.generation, oldEnvelope.generation)
    }

    func testLogoutWaitsForInFlightRefreshThenRemovesRotatedCredentials() async throws {
        let oldEnvelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "old-access", refreshToken: "old-refresh"),
            userID: 42
        )
        let storage = RecordingCredentialStorage(
            envelope: oldEnvelope,
            legacy: LegacyCredentialMaterial(
                accessToken: "old-access",
                refreshToken: "old-refresh"
            )
        )
        let exchangeGate = CredentialTestGate()
        let exchangeStarted = expectation(description: "refresh holds credential lock")
        let exchange = RecordingRefreshExchange { _, _ in
            exchangeStarted.fulfill()
            await exchangeGate.wait()
            return CredentialTokens(
                accessToken: "rotated-access",
                refreshToken: "rotated-refresh"
            )
        }
        let session = makeCredentialSession(storage: storage, exchange: exchange)

        let refreshTask = Task {
            try await session.refreshAfterRejection(rejectedAccessToken: "old-access")
        }
        await fulfillment(of: [exchangeStarted], timeout: 1)

        let clearStarted = expectation(description: "logout clear queued")
        let clearTask = Task {
            clearStarted.fulfill()
            return try await session.clearCredentials(ifCurrent: nil)
        }
        await fulfillment(of: [clearStarted], timeout: 1)
        await exchangeGate.open()

        let refreshedAccessToken = try await refreshTask.value
        let didClear = try await clearTask.value
        XCTAssertEqual(refreshedAccessToken, "rotated-access")
        XCTAssertTrue(didClear)
        XCTAssertNil(storage.envelope)
        XCTAssertNil(storage.legacy)
    }

    func testOldTerminalEventCannotClearNewCredentialPublication() async throws {
        let oldEnvelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "old-access", refreshToken: "old-refresh"),
            userID: 42
        )
        let storage = RecordingCredentialStorage(
            envelope: oldEnvelope,
            legacy: LegacyCredentialMaterial(
                accessToken: "old-access",
                refreshToken: "old-refresh"
            )
        )
        let session = makeCredentialSession(storage: storage)
        let oldEvent = CredentialTerminalEvent(
            generation: oldEnvelope.generation.uuidString.lowercased(),
            userID: oldEnvelope.userID
        )

        try await session.publishAuthenticated(
            tokens: CredentialTokens(
                accessToken: "new-access",
                refreshToken: "new-refresh"
            ),
            userID: 84
        )
        let didClear = try await session.clearCredentials(ifCurrent: oldEvent)

        XCTAssertFalse(didClear)
        XCTAssertEqual(storage.envelope?.userID, 84)
        XCTAssertEqual(storage.envelope?.tokens.accessToken, "new-access")
    }

    func testCredentialClearReportsDeleteFailureAndRetainsMaterial() async {
        let envelope = CredentialEnvelope(
            tokens: CredentialTokens(accessToken: "access", refreshToken: "refresh"),
            userID: 42
        )
        let storage = RecordingCredentialStorage(
            envelope: envelope,
            legacy: LegacyCredentialMaterial(
                accessToken: "access",
                refreshToken: "refresh"
            ),
            deleteFails: true
        )
        let session = makeCredentialSession(storage: storage)

        do {
            _ = try await session.clearCredentials(ifCurrent: nil)
            XCTFail("Expected secure credential deletion failure")
        } catch {
            XCTAssertEqual(error as? CredentialStorageError, .deleteFailed)
        }
        XCTAssertEqual(storage.envelope, envelope)
        XCTAssertEqual(storage.legacy?.completeTokens, envelope.tokens)
    }

    func testTokenStoreCredentialDeletionReportsFailedCompatibilityLeg() throws {
        let tokenStore = RecordingAuthTokenStore(failedDeleteKeys: [.accessToken])
        let storage = TokenStoreCredentialStorage(tokenStore: tokenStore)
        try storage.publishEnvelopeAndLegacy(
            CredentialEnvelope(
                tokens: CredentialTokens(accessToken: "access", refreshToken: "refresh"),
                userID: 42
            )
        )

        XCTAssertThrowsError(try storage.deleteCredentialMaterial()) { error in
            XCTAssertEqual(error as? CredentialStorageError, .deleteFailed)
        }
        XCTAssertEqual(tokenStore.getToken(key: .accessToken), "access")
    }

    func testProductionCooldownDoesNotReuseAccessTokenThatWasJustRejected() async throws {
        let storage = RecordingCredentialStorage(
            envelope: CredentialEnvelope(
                tokens: CredentialTokens(
                    accessToken: "access-1",
                    refreshToken: "refresh-1"
                ),
                userID: 42
            ),
            legacy: LegacyCredentialMaterial(
                accessToken: "access-1",
                refreshToken: "refresh-1"
            )
        )
        let exchange = RecordingRefreshExchange { refreshToken, _ in
            switch refreshToken {
            case "refresh-1":
                return CredentialTokens(
                    accessToken: "access-2",
                    refreshToken: "refresh-2"
                )
            case "refresh-2":
                return CredentialTokens(
                    accessToken: "access-3",
                    refreshToken: "refresh-3"
                )
            default:
                throw ClientFailure.unexpected
            }
        }
        let session = makeCredentialSession(
            storage: storage,
            exchange: exchange,
            cooldownSeconds: 10
        )

        let first = try await session.refreshAfterRejection(
            rejectedAccessToken: "access-1"
        )
        let staleOriginalRequest = try await session.refreshAfterRejection(
            rejectedAccessToken: "access-1"
        )
        let second = try await session.refreshAfterRejection(
            rejectedAccessToken: "access-2"
        )

        XCTAssertEqual(first, "access-2")
        XCTAssertEqual(staleOriginalRequest, "access-2")
        XCTAssertEqual(second, "access-3")
        XCTAssertEqual(exchange.callCount, 2)
        XCTAssertEqual(storage.envelope?.tokens.accessToken, "access-3")
    }

    func testAccountPublicationInvalidatesProductionRefreshCooldown() async throws {
        let storage = RecordingCredentialStorage(
            envelope: CredentialEnvelope(
                tokens: CredentialTokens(
                    accessToken: "shared-rejected-access",
                    refreshToken: "account-a-refresh"
                ),
                userID: 42
            ),
            legacy: LegacyCredentialMaterial(
                accessToken: "shared-rejected-access",
                refreshToken: "account-a-refresh"
            )
        )
        let exchange = RecordingRefreshExchange { refreshToken, _ in
            switch refreshToken {
            case "account-a-refresh":
                return CredentialTokens(
                    accessToken: "account-a-next-access",
                    refreshToken: "account-a-next-refresh"
                )
            case "account-b-refresh":
                return CredentialTokens(
                    accessToken: "account-b-next-access",
                    refreshToken: "account-b-next-refresh"
                )
            default:
                throw ClientFailure.unexpected
            }
        }
        let session = makeCredentialSession(
            storage: storage,
            exchange: exchange,
            cooldownSeconds: 10
        )

        let accountAToken = try await session.refreshAfterRejection(
            rejectedAccessToken: "shared-rejected-access"
        )
        try await session.publishAuthenticated(
            tokens: CredentialTokens(
                // Reuse the same rejected-token value deliberately. The
                // account publication, not token uniqueness, must fence the
                // prior account's in-flight/cooldown result.
                accessToken: "shared-rejected-access",
                refreshToken: "account-b-refresh"
            ),
            userID: 84
        )
        let accountBToken = try await session.refreshAfterRejection(
            rejectedAccessToken: "shared-rejected-access"
        )

        XCTAssertEqual(accountAToken, "account-a-next-access")
        XCTAssertEqual(accountBToken, "account-b-next-access")
        XCTAssertEqual(exchange.callCount, 2)
        XCTAssertEqual(storage.envelope?.userID, 84)
        XCTAssertEqual(storage.envelope?.tokens.accessToken, "account-b-next-access")
    }

    private func makeCredentialSession(
        storage: any CredentialMaterialStoring,
        exchange: (any RefreshTokenExchanging)? = nil,
        attemptStore: (any RefreshAttemptStoring)? = nil,
        cooldownSeconds: TimeInterval = 0,
        terminalHandler: @escaping @Sendable (CredentialTerminalEvent) -> Void = { _ in }
    ) -> CredentialSession {
        let lockURL = FileManager.default.temporaryDirectory.appendingPathComponent(
            "credential-session-test-\(UUID().uuidString).lock"
        )
        return CredentialSession(
            storage: storage,
            exchange: exchange ?? RecordingRefreshExchange { _, _ in
                throw ClientFailure.unexpected
            },
            processLock: AuthRefreshProcessLock(fileURLProvider: { lockURL }),
            attemptStore: attemptStore ?? RecordingRefreshAttemptStore(),
            cooldownSeconds: cooldownSeconds,
            terminalHandler: terminalHandler
        )
    }

    private func credentialUser() -> User {
        User(
            id: 42,
            appleId: "apple-42",
            email: "reader@example.com",
            fullName: "Reader",
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

private final class RecordingCredentialStorage: CredentialMaterialStoring,
    @unchecked Sendable
{
    private let lock = NSLock()
    private var storedEnvelope: CredentialEnvelope?
    private var storedLegacy: LegacyCredentialMaterial?
    private var storedPendingPublication: CredentialPublication?
    private var storedUserID: String?
    private let isUnavailable: Bool
    private let publishLegacyFails: Bool
    private let deleteFails: Bool

    init(
        envelope: CredentialEnvelope? = nil,
        legacy: LegacyCredentialMaterial? = nil,
        pendingPublication: CredentialPublication? = nil,
        isUnavailable: Bool = false,
        publishLegacyFails: Bool = false,
        deleteFails: Bool = false
    ) {
        storedEnvelope = envelope
        storedLegacy = legacy
        storedPendingPublication = pendingPublication
        storedUserID = envelope.map { String($0.userID) }
        self.isUnavailable = isUnavailable
        self.publishLegacyFails = publishLegacyFails
        self.deleteFails = deleteFails
    }

    var envelope: CredentialEnvelope? {
        get { lock.withLock { storedEnvelope } }
        set { lock.withLock { storedEnvelope = newValue } }
    }

    var legacy: LegacyCredentialMaterial? {
        get { lock.withLock { storedLegacy } }
        set { lock.withLock { storedLegacy = newValue } }
    }

    func readEnvelope() -> CredentialStoreRead<CredentialEnvelope> {
        if isUnavailable { return .unavailable }
        return lock.withLock {
            storedEnvelope.map(CredentialStoreRead.value) ?? .missing
        }
    }

    func readLegacyMaterial() -> CredentialStoreRead<LegacyCredentialMaterial> {
        if isUnavailable { return .unavailable }
        return lock.withLock {
            storedLegacy.map(CredentialStoreRead.value) ?? .missing
        }
    }

    func readPendingPublication() -> CredentialStoreRead<CredentialPublication> {
        if isUnavailable { return .unavailable }
        return lock.withLock {
            storedPendingPublication.map(CredentialStoreRead.value) ?? .missing
        }
    }

    func publishEnvelopeAndLegacy(_ envelope: CredentialEnvelope) throws {
        let publication = lock.withLock {
            CredentialPublication(
                target: envelope,
                baselineEnvelope: storedEnvelope,
                baselineAccessToken: storedLegacy?.accessToken,
                baselineRefreshToken: storedLegacy?.refreshToken,
                baselineUserID: storedUserID
            )
        }
        lock.withLock { storedPendingPublication = publication }
        try completePendingPublication(publication)
    }

    func completePendingPublication(_ publication: CredentialPublication) throws {
        try lock.withLock {
            guard storedPendingPublication == publication else {
                throw CredentialStorageError.unavailable
            }
            let snapshot = CredentialPublicationSnapshot(
                envelope: storedEnvelope,
                accessToken: storedLegacy?.accessToken,
                refreshToken: storedLegacy?.refreshToken,
                userID: storedUserID
            )
            guard publication.permits(snapshot) else {
                if publication.isClearlySuperseded(by: snapshot) {
                    storedPendingPublication = nil
                    return
                }
                throw CredentialStorageError.unavailable
            }
            storedEnvelope = publication.target
            storedLegacy = LegacyCredentialMaterial(
                accessToken: publication.target.tokens.accessToken,
                refreshToken: publication.target.tokens.refreshToken
            )
            storedUserID = String(publication.target.userID)
            storedPendingPublication = nil
        }
    }

    func publishLegacy(_ tokens: CredentialTokens) throws {
        if publishLegacyFails {
            throw CredentialStorageError.writeFailed
        }
        lock.withLock {
            storedLegacy = LegacyCredentialMaterial(
                accessToken: tokens.accessToken,
                refreshToken: tokens.refreshToken
            )
        }
    }

    func deleteCredentialMaterial() throws {
        if deleteFails {
            throw CredentialStorageError.deleteFailed
        }
        lock.withLock {
            storedEnvelope = nil
            storedLegacy = nil
            storedPendingPublication = nil
            storedUserID = nil
        }
    }
}

private final class RecordingAuthTokenStore: AuthTokenStore, @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [KeychainManager.KeychainKey: String] = [:]
    private var recordedKeys: [KeychainManager.KeychainKey] = []
    private let failedDeleteKeys: Set<KeychainManager.KeychainKey>

    init(failedDeleteKeys: Set<KeychainManager.KeychainKey> = []) {
        self.failedDeleteKeys = failedDeleteKeys
    }

    var savedKeys: [KeychainManager.KeychainKey] {
        lock.withLock { recordedKeys }
    }

    func getToken(key: KeychainManager.KeychainKey) -> String? {
        lock.withLock { storage[key] }
    }

    func saveToken(_ token: String, key: KeychainManager.KeychainKey) {
        lock.withLock {
            storage[key] = token
            recordedKeys.append(key)
        }
    }

    func deleteToken(key: KeychainManager.KeychainKey) {
        guard !failedDeleteKeys.contains(key) else { return }
        lock.withLock { _ = storage.removeValue(forKey: key) }
    }

    func deleteTokenReportingStatus(key: KeychainManager.KeychainKey) -> Bool {
        deleteToken(key: key)
        return !failedDeleteKeys.contains(key) && getToken(key: key) == nil
    }

    func clearAll() {
        lock.withLock { storage.removeAll() }
    }
}

private final class RecordingRefreshExchange: RefreshTokenExchanging, @unchecked Sendable {
    typealias Operation = @Sendable (String, String) async throws -> CredentialTokens

    private let lock = NSLock()
    private var recordedCallCount = 0
    private let operation: Operation

    init(operation: @escaping Operation) {
        self.operation = operation
    }

    var callCount: Int { lock.withLock { recordedCallCount } }

    func exchange(refreshToken: String, attemptID: String) async throws -> CredentialTokens {
        lock.withLock { recordedCallCount += 1 }
        return try await operation(refreshToken, attemptID)
    }
}

private final class RecordingRefreshAttemptStore: RefreshAttemptStoring,
    @unchecked Sendable
{
    func attemptID(for refreshToken: String) throws -> String {
        "attempt-\(refreshToken)"
    }

    func clearAttempt(for refreshToken: String) {
        _ = refreshToken
    }
}

private final class RecordingRefreshAttemptPersistence: RefreshAttemptPersisting,
    @unchecked Sendable
{
    private let lock = NSLock()
    private let writeFails: Bool
    private var storedEnvelope: String?
    private var recordedWriteCount = 0

    init(writeFails: Bool = false) {
        self.writeFails = writeFails
    }

    var writeCount: Int {
        lock.withLock { recordedWriteCount }
    }

    func readRefreshAttempt() -> RefreshAttemptPersistenceRead {
        lock.withLock {
            storedEnvelope.map(RefreshAttemptPersistenceRead.value) ?? .missing
        }
    }

    func persistRefreshAttempt(_ encodedEnvelope: String) throws {
        try lock.withLock {
            recordedWriteCount += 1
            if writeFails {
                throw RefreshAttemptStoreError.writeFailed
            }
            storedEnvelope = encodedEnvelope
        }
    }

    func deleteRefreshAttempt() {
        lock.withLock { storedEnvelope = nil }
    }
}

private actor CredentialTestGate {
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

private final class CredentialLockedValues<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [Value] = []

    var values: [Value] { lock.withLock { storage } }

    func append(_ value: Value) {
        lock.withLock { storage.append(value) }
    }
}
