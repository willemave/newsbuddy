import Foundation
import XCTest
@testable import newsly

@MainActor
private func makeActiveLifecycle() -> AppLifecycle {
    let lifecycle = AppLifecycle()
    lifecycle.record(.active)
    return lifecycle
}

@MainActor
final class ChatSessionViewModelTests: XCTestCase {
    func testDefaultChatDictationUsesRecordThenTranscribeService() {
        let service = ChatDependencies.live.transcriptionService as AnyObject

        XCTAssertTrue(service === VoiceDictationService.shared)
    }

    func testToggleVoiceRecordingStartsRecordingOnFirstTap() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Ignored")
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(transcriptionService: transcriptionService),
            initialVoiceDictationAvailable: true
        )

        await viewModel.toggleVoiceRecording()

        XCTAssertTrue(viewModel.isRecording)
        XCTAssertFalse(viewModel.isTranscribing)
        XCTAssertEqual(transcriptionService.startCallCount, 1)
        XCTAssertEqual(transcriptionService.stopCallCount, 0)
    }

    func testToggleVoiceRecordingStopsRecordingOnSecondTapAndSendsTranscript() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Final transcript")
        let chatService = makeSuccessfulVoiceSendService()
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: transcriptionService,
                chatService: chatService
            ),
            initialVoiceDictationAvailable: true
        )

        await viewModel.toggleVoiceRecording()
        await viewModel.toggleVoiceRecording()

        XCTAssertEqual(chatService.sentMessages.map { $0.message }, ["Final transcript"])
        XCTAssertEqual(viewModel.inputText, "")
        XCTAssertFalse(viewModel.isRecording)
        XCTAssertFalse(viewModel.isTranscribing)
        XCTAssertEqual(transcriptionService.startCallCount, 1)
        XCTAssertEqual(transcriptionService.stopCallCount, 1)
    }

    func testToggleVoiceRecordingIgnoresTapWhileTranscribing() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Ignored")
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(transcriptionService: transcriptionService),
            initialVoiceDictationAvailable: true
        )

        await viewModel.toggleVoiceRecording()
        transcriptionService.emit(.stateChange(.transcribing))
        let didEnterTranscribingState = await waitUntil { viewModel.isTranscribing }

        await viewModel.toggleVoiceRecording()

        XCTAssertTrue(didEnterTranscribingState)
        XCTAssertEqual(transcriptionService.startCallCount, 1)
        XCTAssertEqual(transcriptionService.stopCallCount, 0)
        XCTAssertFalse(viewModel.isRecording)
        XCTAssertTrue(viewModel.isTranscribing)
    }

    func testStopVoiceRecordingSendsTranscriptWithoutDraftPreview() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Final transcript")
        let chatService = makeSuccessfulVoiceSendService()
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: transcriptionService,
                chatService: chatService
            ),
            initialVoiceDictationAvailable: true
        )

        await viewModel.toggleVoiceRecording()
        XCTAssertEqual(viewModel.inputText, "")

        await viewModel.toggleVoiceRecording()

        XCTAssertEqual(chatService.sentMessages.map { $0.message }, ["Final transcript"])
        XCTAssertEqual(viewModel.inputText, "")
        XCTAssertFalse(viewModel.isRecording)
        XCTAssertFalse(viewModel.isTranscribing)
        XCTAssertEqual(viewModel.timeline.map(\.message.content), ["Final transcript", "Assistant reply"])
    }

    func testStopVoiceRecordingSendsExistingDraftAndTranscript() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "second thought")
        let chatService = makeSuccessfulVoiceSendService()
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: transcriptionService,
                chatService: chatService
            ),
            initialVoiceDictationAvailable: true
        )

        viewModel.inputText = "First draft"
        await viewModel.toggleVoiceRecording()

        await viewModel.toggleVoiceRecording()

        XCTAssertEqual(chatService.sentMessages.map { $0.message }, ["First draft second thought"])
        XCTAssertEqual(viewModel.inputText, "")
    }

    func testSilenceAutoStopSendsTranscriptWithoutManualStop() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Auto transcript")
        let chatService = makeSuccessfulVoiceSendService()
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: transcriptionService,
                chatService: chatService
            ),
            initialVoiceDictationAvailable: true
        )

        await viewModel.toggleVoiceRecording()
        await transcriptionService.simulateSilenceAutoStop()
        let didSendTranscript = await waitUntil {
            chatService.sentMessages.map { $0.message } == ["Auto transcript"]
        }

        XCTAssertTrue(didSendTranscript)
        XCTAssertEqual(chatService.sentMessages.map { $0.message }, ["Auto transcript"])
        XCTAssertEqual(viewModel.inputText, "")
        XCTAssertFalse(viewModel.isRecording)
        XCTAssertFalse(viewModel.isTranscribing)
        XCTAssertEqual(transcriptionService.stopCallCount, 0)
    }

    func testMaximumDurationAutoStopSendsTranscriptWithoutManualStop() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Maximum transcript")
        let chatService = makeSuccessfulVoiceSendService()
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: transcriptionService,
                chatService: chatService
            ),
            initialVoiceDictationAvailable: true
        )

        await viewModel.toggleVoiceRecording()
        await transcriptionService.simulateAutomaticStop(reason: .maximumDuration)

        let didSend = await waitUntil {
            chatService.sentMessages.map(\.message) == ["Maximum transcript"]
        }
        XCTAssertTrue(didSend)
        XCTAssertFalse(viewModel.isRecording)
        XCTAssertFalse(viewModel.isTranscribing)
    }

    func testEmptyVoiceTranscriptShowsRetryableActionError() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "   ")
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(transcriptionService: transcriptionService),
            initialVoiceDictationAvailable: true
        )

        await viewModel.toggleVoiceRecording()
        await viewModel.toggleVoiceRecording()

        XCTAssertEqual(viewModel.errorMessage, "I didn't catch that. Try again.")
        XCTAssertNil(viewModel.loadErrorMessage)
    }

    func testNoSpeechAutoStopShowsRetryableErrorWithoutSending() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Ignored")
        let chatService = makeSuccessfulVoiceSendService()
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: transcriptionService,
                chatService: chatService
            ),
            initialVoiceDictationAvailable: true
        )

        await viewModel.toggleVoiceRecording()
        await transcriptionService.simulateNoSpeechTimeout()
        let didShowError = await waitUntil {
            viewModel.errorMessage == "No speech detected. Try again."
        }

        XCTAssertTrue(didShowError)
        XCTAssertFalse(viewModel.isRecording)
        XCTAssertFalse(viewModel.isTranscribing)
        XCTAssertEqual(chatService.sentMessages.count, 0)
        XCTAssertEqual(transcriptionService.stopCallCount, 0)
    }

    func testConsecutiveSilenceAutoStopsRemainSubscribed() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Auto transcript")
        let chatService = makeSuccessfulVoiceSendService()
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: transcriptionService,
                chatService: chatService
            ),
            initialVoiceDictationAvailable: true
        )

        await viewModel.toggleVoiceRecording()
        await transcriptionService.simulateSilenceAutoStop()
        let didSendFirstTranscript = await waitUntil {
            chatService.sentMessages.count == 1 && !viewModel.isVoiceActionInFlight
        }

        await viewModel.toggleVoiceRecording()
        await transcriptionService.simulateSilenceAutoStop()
        let didSendSecondTranscript = await waitUntil {
            chatService.sentMessages.count == 2 && !viewModel.isVoiceActionInFlight
        }

        XCTAssertTrue(didSendFirstTranscript)
        XCTAssertTrue(didSendSecondTranscript)
        XCTAssertEqual(
            chatService.sentMessages.map(\.message),
            ["Auto transcript", "Auto transcript"]
        )
    }

    func testMessagesSentWhileAgentIsProcessingDrainInFIFOOrder() async {
        let firstTurnGate = AsyncGate()
        var sentTexts: [String] = []
        let chatService = MockChatSessionService(
            getSessionHandler: { _ in
                let messages = sentTexts.enumerated().flatMap { index, text in
                    [
                        Self.message(
                            id: 100 + index,
                            role: .user,
                            content: text,
                            status: .completed
                        ),
                        Self.message(
                            id: 200 + index,
                            role: .assistant,
                            content: "Reply \(index + 1)",
                            status: .completed
                        ),
                    ]
                }
                return ChatSessionDetail(session: Self.session(), messages: messages)
            },
            sendMessageHandler: { sessionId, message in
                sentTexts.append(message)
                let turn = sentTexts.count
                return SendChatMessageResponse(
                    sessionId: sessionId,
                    userMessage: Self.message(
                        id: 100 + turn,
                        role: .user,
                        content: message,
                        status: .processing
                    ),
                    messageId: 500 + turn,
                    status: .processing
                )
            },
            messageStatusHandler: { messageId in
                if messageId == 501 {
                    await firstTurnGate.wait()
                }
                return MessageStatusResponse(
                    messageId: messageId,
                    status: .completed,
                    assistantMessage: Self.message(
                        id: messageId + 1_000,
                        role: .assistant,
                        content: "Completed \(messageId)",
                        status: .completed
                    ),
                    error: nil
                )
            }
        )
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService
            )
        )

        viewModel.inputText = "First"
        viewModel.performSendMessage()
        let didStartFirstTurn = await waitUntil {
            chatService.sentMessages.map(\.message) == ["First"] && viewModel.isSending
        }

        viewModel.inputText = "Second"
        viewModel.performSendMessage()
        viewModel.inputText = "Third"
        viewModel.performSendMessage()

        XCTAssertTrue(didStartFirstTurn)
        XCTAssertEqual(chatService.sentMessages.map(\.message), ["First"])
        XCTAssertEqual(viewModel.timeline.filter(\.isQueued).map(\.message.content), ["Second", "Third"])
        XCTAssertEqual(viewModel.inputText, "")

        await firstTurnGate.open()
        let didDrainQueue = await waitUntil {
            chatService.sentMessages.map(\.message) == ["First", "Second", "Third"]
                && !viewModel.isSending
        }

        XCTAssertTrue(didDrainQueue)
        XCTAssertEqual(chatService.sentMessages.map(\.message), ["First", "Second", "Third"])
        XCTAssertTrue(viewModel.timeline.allSatisfy { !$0.isQueued })
    }

    func testStreamingPartialIsReplacedInPlaceByFinalAssistantMessage() async {
        let finalGate = AsyncGate()
        let userMessage = Self.message(
            id: 101,
            role: .user,
            content: "Explain this",
            status: .processing
        )
        let partialMessage = ChatMessage(
            id: -501,
            sourceMessageId: 501,
            role: .assistant,
            timestamp: Date(),
            content: "A partial explanation",
            status: .processing
        )
        let finalMessage = ChatMessage(
            id: -501,
            sourceMessageId: 501,
            role: .assistant,
            timestamp: Date(),
            content: "The complete explanation",
            status: .completed
        )
        var statusCallCount = 0
        var didComplete = false
        let chatService = MockChatSessionService(
            getSessionHandler: { _ in
                ChatSessionDetail(
                    session: Self.session(),
                    messages: didComplete ? [userMessage, finalMessage] : [userMessage]
                )
            },
            sendMessageHandler: { sessionId, _ in
                SendChatMessageResponse(
                    sessionId: sessionId,
                    userMessage: userMessage,
                    messageId: 501,
                    status: .processing
                )
            },
            messageStatusHandler: { messageId in
                statusCallCount += 1
                if statusCallCount == 1 {
                    return MessageStatusResponse(
                        messageId: messageId,
                        status: .processing,
                        partialAssistantMessage: partialMessage,
                        streamGeneration: 0,
                        streamRevision: 1
                    )
                }
                await finalGate.wait()
                didComplete = true
                return MessageStatusResponse(
                    messageId: messageId,
                    status: .completed,
                    assistantMessage: finalMessage
                )
            }
        )
        let registry = ChatMessageCompletionRegistry(
            fetchStatus: { try await chatService.getMessageStatus(messageId: $0) },
            policy: ChatMessageCompletionPollingPolicy(
                delaysNanoseconds: [0, 0],
                progressDelayNanoseconds: 0,
                absoluteMaximumRequestCount: 4
            ),
            sleep: { _ in }
        )
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService,
                messageCompletionRegistry: registry
            )
        )

        let sendTask = Task { await viewModel.sendMessage(text: "Explain this") }
        let didShowPartial = await waitUntil {
            viewModel.timeline.contains { $0.message.content == "A partial explanation" }
        }

        XCTAssertTrue(didShowPartial)
        XCTAssertTrue(viewModel.hasVisiblePartialResponse)
        XCTAssertEqual(viewModel.timeline.filter { $0.message.isAssistant }.count, 1)

        await finalGate.open()
        await sendTask.value

        let assistantRows = viewModel.timeline.filter { $0.message.isAssistant }
        XCTAssertEqual(assistantRows.count, 1)
        XCTAssertEqual(assistantRows.first?.message.content, "The complete explanation")
        XCTAssertFalse(viewModel.hasVisiblePartialResponse)
    }

    func testSessionLoadFailureDoesNotMasqueradeAsAnActionFailure() async {
        let chatService = MockChatSessionService(getSessionHandler: { _ in
            throw ChatServiceError.timeout
        })
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService
            )
        )

        await viewModel.loadSession()

        XCTAssertNotNil(viewModel.loadErrorMessage)
        XCTAssertNil(viewModel.errorMessage)
    }

    func testInactiveInterruptionDoesNotSuspendAcceptedPolling() async {
        let completionGate = AsyncGate()
        let lifecycle = makeActiveLifecycle()
        let chatService = MockChatSessionService(
            sendMessageHandler: { sessionId, message in
                SendChatMessageResponse(
                    sessionId: sessionId,
                    userMessage: Self.message(
                        id: 101,
                        role: .user,
                        content: message,
                        status: .processing
                    ),
                    messageId: 501,
                    status: .processing
                )
            },
            messageStatusHandler: { messageId in
                await completionGate.wait()
                return MessageStatusResponse(
                    messageId: messageId,
                    status: .completed,
                    assistantMessage: Self.message(
                        id: 201,
                        role: .assistant,
                        content: "Completed through interruption",
                        status: .completed
                    )
                )
            }
        )
        let activeSessionManager = ActiveChatSessionManager(startsPolling: false)
        let viewModel = ChatSessionViewModel(
            lifecycle: lifecycle,
            route: ChatSessionRoute(session: Self.session(
                contentId: 7,
                articleTitle: "Tracked Article"
            )),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService,
                activeSessionManager: activeSessionManager
            )
        )

        viewModel.performSendMessage(text: "Keep polling")
        let didStartPolling = await waitUntil {
            chatService.messageStatusCallCount > 0 && viewModel.isSending
        }

        lifecycle.record(.inactive)
        viewModel.handleLifecyclePhaseChange()
        lifecycle.record(.active)
        viewModel.handleLifecyclePhaseChange()

        XCTAssertTrue(didStartPolling)
        XCTAssertTrue(viewModel.isSending)
        XCTAssertNil(activeSessionManager.getSession(forContentId: 7))

        await completionGate.open()
        let didComplete = await waitUntil {
            viewModel.timeline.contains { $0.message.content == "Completed through interruption" }
                && !viewModel.isSending
        }

        XCTAssertTrue(didComplete)
        XCTAssertEqual(chatService.sentMessages.map(\.message), ["Keep polling"])
        XCTAssertEqual(lifecycle.activation?.generation, 1)
    }

    func testBackgroundPreservesPreAckSendAndActivationReconcilesWithoutResend() async {
        let acknowledgementGate = AsyncGate()
        let lifecycle = makeActiveLifecycle()
        let chatService = MockChatSessionService(
            getSessionHandler: { _ in
                ChatSessionDetail(
                    session: Self.session(
                        contentId: 7,
                        articleTitle: "Tracked Article"
                    ),
                    messages: [
                        Self.message(id: 101, role: .user, content: "One send", status: .completed),
                        Self.message(id: 201, role: .assistant, content: "Reconciled reply", status: .completed),
                    ]
                )
            },
            sendMessageHandler: { sessionId, message in
                await acknowledgementGate.wait()
                return SendChatMessageResponse(
                    sessionId: sessionId,
                    userMessage: Self.message(
                        id: 101,
                        role: .user,
                        content: message,
                        status: .processing
                    ),
                    messageId: 501,
                    status: .processing
                )
            }
        )
        let activeSessionManager = ActiveChatSessionManager(startsPolling: false)
        let viewModel = ChatSessionViewModel(
            lifecycle: lifecycle,
            route: ChatSessionRoute(session: Self.session(
                contentId: 7,
                articleTitle: "Tracked Article"
            )),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService,
                activeSessionManager: activeSessionManager
            )
        )

        viewModel.performSendMessage(text: "One send")
        let didStartSend = await waitUntil {
            chatService.sentMessages.count == 1 && viewModel.isSending
        }

        lifecycle.record(.inactive)
        viewModel.handleLifecyclePhaseChange()
        lifecycle.record(.background)
        viewModel.handleLifecyclePhaseChange()

        XCTAssertTrue(didStartSend)
        XCTAssertTrue(viewModel.isSending, "A pre-ack command must survive backgrounding")
        XCTAssertNil(activeSessionManager.getSession(forContentId: 7))

        await acknowledgementGate.open()
        let didHandOffAcceptedTurn = await waitUntil {
            activeSessionManager.getSession(forContentId: 7)?.messageId == 501
                && !viewModel.isSending
        }
        XCTAssertTrue(didHandOffAcceptedTurn)

        lifecycle.record(.inactive)
        viewModel.handleLifecyclePhaseChange()
        lifecycle.record(.active)
        viewModel.handleLifecyclePhaseChange()
        await viewModel.resumeAfterActivationIfNeeded()

        XCTAssertEqual(chatService.sentMessages.map(\.message), ["One send"])
        XCTAssertTrue(viewModel.timeline.contains { $0.message.content == "Reconciled reply" })
        XCTAssertNil(activeSessionManager.getSession(forContentId: 7))
        XCTAssertEqual(lifecycle.activation?.generation, 2)
    }

    func testQueuedSendResumesAfterForegroundWhenActiveSendFailsWhileInactive() async {
        let firstSendGate = AsyncGate()
        let chatService = MockChatSessionService(
            getSessionHandler: { _ in
                ChatSessionDetail(
                    session: Self.session(),
                    messages: [
                        Self.message(id: 102, role: .user, content: "Second", status: .completed),
                        Self.message(id: 202, role: .assistant, content: "Reply", status: .completed),
                    ]
                )
            },
            sendMessageHandler: { sessionId, message in
                if message == "First" {
                    await firstSendGate.wait()
                    throw ClientFailure.connectivity(.networkConnectionLost)
                }
                return SendChatMessageResponse(
                    sessionId: sessionId,
                    userMessage: Self.message(
                        id: 102,
                        role: .user,
                        content: message,
                        status: .processing
                    ),
                    messageId: 502,
                    status: .processing
                )
            },
            messageStatusHandler: { messageId in
                MessageStatusResponse(
                    messageId: messageId,
                    status: .completed,
                    assistantMessage: Self.message(
                        id: 202,
                        role: .assistant,
                        content: "Reply",
                        status: .completed
                    ),
                    error: nil
                )
            }
        )
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService
            )
        )

        viewModel.performSendMessage(text: "First")
        let didStartFirst = await waitUntil { viewModel.isSending }
        viewModel.performSendMessage(text: "Second")
        viewModel.handleDisappear()
        await firstSendGate.open()
        let didFinishFirst = await waitUntil { !viewModel.isSending }

        XCTAssertTrue(didStartFirst)
        XCTAssertTrue(didFinishFirst)
        XCTAssertEqual(chatService.sentMessages.map(\.message), ["First"])
        XCTAssertEqual(viewModel.timeline.filter(\.isQueued).map(\.message.content), ["Second"])

        viewModel.handleAppear()
        let didResumeQueuedSend = await waitUntil {
            chatService.sentMessages.map(\.message) == ["First", "Second"]
                && !viewModel.isSending
        }

        XCTAssertTrue(didResumeQueuedSend)
        XCTAssertTrue(viewModel.timeline.allSatisfy { !$0.isQueued })
    }

    func testCancelCouncilSelectionClearsInFlightState() async {
        let chatService = MockChatSessionService(selectCouncilBranchHandler: { _, _ in
            try await Task.sleep(nanoseconds: 60_000_000_000)
            throw CancellationError()
        })
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(session: Self.session(activeChildSessionId: 200)),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService
            )
        )

        let selectionTask = Task {
            await viewModel.selectCouncilBranch(childSessionId: 201)
        }

        let didStartSelection = await waitUntil { viewModel.selectingCouncilChildSessionId == 201 }
        XCTAssertTrue(didStartSelection)

        viewModel.cancelCouncilSelection()
        await selectionTask.value

        XCTAssertNil(viewModel.selectingCouncilChildSessionId)
        XCTAssertFalse(viewModel.councilSelectionTimedOut)
        XCTAssertNil(viewModel.errorMessage)
    }

    func testRetryCouncilCandidateAppliesReturnedDetail() async {
        let retriedMessage = ChatMessage(
            id: 9,
            sourceMessageId: 9,
            role: .assistant,
            timestamp: ServerDate.parse("2026-04-01T10:00:00Z")!,
            content: "Ben Thompson regenerated.",
            councilCandidates: [
                CouncilCandidate(
                    personaId: "ben_thompson",
                    personaName: "Ben Thompson",
                    childSessionId: 201,
                    content: "Ben Thompson regenerated.",
                    status: "completed",
                    order: 0
                )
            ],
            activeCouncilChildSessionId: 201
        )
        let detail = ChatSessionDetail(
            session: Self.session(activeChildSessionId: 201),
            messages: [retriedMessage]
        )
        let chatService = MockChatSessionService(retryCouncilBranchHandler: { _, childSessionId in
            XCTAssertEqual(childSessionId, 201)
            return detail
        })
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(session: Self.session(activeChildSessionId: 200)),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService
            )
        )

        await viewModel.retryCouncilCandidate(childSessionId: 201)

        XCTAssertNil(viewModel.retryingCouncilChildSessionId)
        XCTAssertEqual(viewModel.activeCouncilChildSessionId, 201)
        XCTAssertEqual(viewModel.councilCandidates.first?.status, "completed")
        XCTAssertEqual(viewModel.councilCandidates.first?.content, "Ben Thompson regenerated.")
    }

    func testHandleDisappearKeepsPreAckSendAliveAndHandsOffAfterServerAck() async {
        let ackGate = AsyncGate()
        let chatService = MockChatSessionService(
            sendMessageHandler: { sessionId, message in
                await ackGate.wait()
                return SendChatMessageResponse(
                    sessionId: sessionId,
                    userMessage: Self.message(id: 101, role: .user, content: message, status: .processing),
                    messageId: 501,
                    status: .processing
                )
            },
            messageStatusHandler: { messageId in
                MessageStatusResponse(
                    messageId: messageId,
                    status: .completed,
                    assistantMessage: Self.message(
                        id: 201,
                        role: .assistant,
                        content: "Should not poll while inactive",
                        status: .completed
                    ),
                    error: nil
                )
            }
        )
        let activeSessionManager = ActiveChatSessionManager(startsPolling: false)
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(session: Self.session(
                contentId: 7,
                articleTitle: "Tracked Article"
            )),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService,
                activeSessionManager: activeSessionManager
            )
        )

        viewModel.inputText = "Hello"
        viewModel.performSendMessage()
        let didStartSend = await waitUntil {
            chatService.sentMessages.count == 1 && viewModel.isSending
        }

        XCTAssertTrue(didStartSend)
        XCTAssertTrue(viewModel.isSending)
        XCTAssertEqual(viewModel.timeline.last?.message.content, "Hello")

        viewModel.handleDisappear()

        XCTAssertTrue(viewModel.isSending)
        XCTAssertNil(activeSessionManager.getSession(forContentId: 7))

        await ackGate.open()
        let didHandOff = await waitUntil {
            activeSessionManager.getSession(forContentId: 7)?.messageId == 501
        }

        XCTAssertTrue(didHandOff)
        XCTAssertFalse(viewModel.isSending)
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertEqual(viewModel.timeline.last?.message.content, "Hello")
        XCTAssertEqual(chatService.messageStatusCallCount, 0)
    }

    func testHandleDisappearCancelsAcceptedPollingWithoutFailingMessage() async {
        let chatService = MockChatSessionService(
            sendMessageHandler: { sessionId, message in
                SendChatMessageResponse(
                    sessionId: sessionId,
                    userMessage: Self.message(id: 101, role: .user, content: message, status: .processing),
                    messageId: 501,
                    status: .processing
                )
            },
            messageStatusHandler: { messageId in
                while true {
                    try Task.checkCancellation()
                    try await Task.sleep(nanoseconds: 10_000_000)
                }
            }
        )
        let activeSessionManager = ActiveChatSessionManager(startsPolling: false)
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(session: Self.session(
                contentId: 7,
                articleTitle: "Tracked Article"
            )),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService,
                activeSessionManager: activeSessionManager
            )
        )

        viewModel.inputText = "Hello"
        viewModel.performSendMessage()
        let didStartPolling = await waitUntil {
            chatService.messageStatusCallCount > 0 && viewModel.isSending
        }

        XCTAssertTrue(didStartPolling)
        XCTAssertTrue(viewModel.isSending)
        XCTAssertEqual(viewModel.timeline.last?.message.content, "Hello")

        viewModel.handleDisappear()
        let didCancelPolling = await waitUntil { !viewModel.isSending }

        XCTAssertTrue(didCancelPolling)
        XCTAssertFalse(viewModel.isSending)
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertEqual(viewModel.timeline.last?.message.content, "Hello")
        XCTAssertFalse(viewModel.timeline.last?.message.hasFailed ?? true)
        XCTAssertEqual(activeSessionManager.getSession(forContentId: 7)?.messageId, 501)
    }

    func testSendMessageSurfacesTransportErrorWhenNotCancelled() async {
        let chatService = MockChatSessionService(sendMessageHandler: { _, _ in
            throw ClientFailure.connectivity(.networkConnectionLost)
        })
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService
            )
        )

        viewModel.inputText = "Hello"

        await viewModel.sendMessage()

        XCTAssertNotNil(viewModel.errorMessage)
        XCTAssertTrue(viewModel.timeline.last?.message.hasFailed ?? false)
        XCTAssertEqual(viewModel.timeline.last?.retryText, "Hello")
    }

    func testHandleDisappearHandsOffContentBackedProcessingMessageToBackgroundTracker() async {
        let session = Self.session(
            contentId: 7,
            articleTitle: "Tracked Article",
            hasPendingMessage: true
        )
        let activeSessionManager = ActiveChatSessionManager(startsPolling: false)
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(
                session: session,
                initialUserMessageText: "Track this",
                initialUserMessageTimestamp: ServerDate.parse("2026-04-01T10:00:00Z")!,
                pendingMessageId: 99
            ),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                activeSessionManager: activeSessionManager
            )
        )

        viewModel.handleDisappear()

        let tracked = activeSessionManager.getSession(forContentId: 7)
        XCTAssertEqual(tracked?.id, 42)
        XCTAssertEqual(tracked?.messageId, 99)
        XCTAssertEqual(tracked?.contentTitle, "Tracked Article")
    }

    func testHandleDisappearBeforeServerAckDoesNotTrackLocalPlaceholder() async {
        let ackGate = AsyncGate()
        let chatService = MockChatSessionService(sendMessageHandler: { _, _ in
            await ackGate.wait()
            throw CancellationError()
        })
        let activeSessionManager = ActiveChatSessionManager(startsPolling: false)
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(session: Self.session(
                contentId: 7,
                articleTitle: "Tracked Article"
            )),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService,
                activeSessionManager: activeSessionManager
            )
        )

        viewModel.inputText = "Track only after backend ack"
        viewModel.performSendMessage()
        let didStartSend = await waitUntil {
            chatService.sentMessages.count == 1 && viewModel.isSending
        }

        XCTAssertTrue(didStartSend)
        XCTAssertTrue(viewModel.isSending)
        XCTAssertEqual(viewModel.timeline.last?.message.content, "Track only after backend ack")

        viewModel.handleDisappear()

        XCTAssertNil(activeSessionManager.getSession(forContentId: 7))
        await ackGate.open()
        _ = await waitUntil { !viewModel.isSending }
    }

    func testEmptyContextualSessionDoesNotAutoGenerateInitialSuggestions() async {
        let chatService = MockChatSessionService(getSessionHandler: { _ in
            ChatSessionDetail(
                session: Self.session(
                    contentId: 7,
                    articleTitle: "Tracked Article",
                    hasMessages: false,
                    councilMode: false
                ),
                messages: []
            )
        })
        let viewModel = ChatSessionViewModel(
            lifecycle: makeActiveLifecycle(),
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService
            )
        )

        await viewModel.loadSession()

        XCTAssertEqual(chatService.initialSuggestionsCallCount, 0)
        XCTAssertFalse(viewModel.isSending)
        XCTAssertTrue(viewModel.timeline.isEmpty)
        XCTAssertEqual(viewModel.session?.contentId, 7)
    }

    private func makeSuccessfulVoiceSendService() -> MockChatSessionService {
        var latestMessage = ""
        return MockChatSessionService(
            getSessionHandler: { _ in
                ChatSessionDetail(
                    session: Self.session(),
                    messages: [
                        Self.message(id: 101, role: .user, content: latestMessage, status: .completed),
                        Self.message(id: 201, role: .assistant, content: "Assistant reply", status: .completed),
                    ]
                )
            },
            sendMessageHandler: { sessionId, message in
                latestMessage = message
                return SendChatMessageResponse(
                    sessionId: sessionId,
                    userMessage: Self.message(id: 101, role: .user, content: message, status: .processing),
                    messageId: 501,
                    status: .processing
                )
            },
            messageStatusHandler: { messageId in
                MessageStatusResponse(
                    messageId: messageId,
                    status: .completed,
                    assistantMessage: Self.message(
                        id: 201,
                        role: .assistant,
                        content: "Assistant reply",
                        status: .completed
                    ),
                    error: nil
                )
            }
        )
    }

    private static func message(
        id: Int,
        role: APIChatMessageRole,
        content: String,
        status: APIMessageProcessingStatus
    ) -> ChatMessage {
        ChatMessage(
            id: id,
            role: role,
            timestamp: ServerDate.parse("2026-04-01T10:00:00Z")!,
            content: content,
            status: status
        )
    }

    private static func session(
        contentId: Int? = nil,
        newsItemId: Int? = nil,
        articleTitle: String? = nil,
        hasPendingMessage: Bool = false,
        activeChildSessionId: Int? = nil,
        hasMessages: Bool = true,
        councilMode: Bool? = true
    ) -> ChatSessionSummary {
        ChatSessionSummary(
            id: 42,
            contentId: contentId,
            newsItemId: newsItemId,
            title: "Chat",
            sessionType: "knowledge_chat",
            topic: nil,
            llmProvider: "openai",
            llmModel: "openai:gpt-5.5",
            createdAt: ServerDate.parse("2026-04-01T10:00:00Z")!,
            updatedAt: nil,
            lastMessageAt: nil,
            articleTitle: articleTitle,
            articleUrl: nil,
            articleSummary: nil,
            articleSource: nil,
            hasPendingMessage: hasPendingMessage,
            isSavedToKnowledge: false,
            hasMessages: hasMessages,
            lastMessagePreview: nil,
            lastMessageRole: nil,
            councilMode: councilMode,
            activeChildSessionId: activeChildSessionId
        )
    }

    private func waitUntil(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<300 {
            if condition() {
                return true
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        return condition()
    }
}

@MainActor
private extension ChatDependencies {
    static func test(
        transcriptionService: any SpeechTranscribing,
        chatService: any ChatSessionServicing = MockChatSessionService(),
        activeSessionManager: ActiveChatSessionManager? = nil,
        messageCompletionRegistry: ChatMessageCompletionRegistry? = nil
    ) -> ChatDependencies {
        let registry = messageCompletionRegistry
            ?? ChatMessageCompletionRegistry(statusService: chatService)
        return ChatDependencies(
            chatService: chatService,
            messageCompletionRegistry: registry,
            transcriptionService: transcriptionService,
            activeSessionManager: activeSessionManager
                ?? ActiveChatSessionManager(
                    messageCompletionRegistry: registry,
                    startsPolling: false
                ),
            refreshTranscriptionAvailability: {
                transcriptionService.isAvailable
            },
            setBackendTranscriptionAvailable: { _ in }
        )
    }
}

private final class MockChatSessionService: ChatSessionServicing {
    private let getSessionHandler: ((Int) async throws -> ChatSessionDetail)?
    private let sendMessageHandler: ((Int, String) async throws -> SendChatMessageResponse)?
    private let messageStatusHandler: ((Int) async throws -> MessageStatusResponse)?
    private let selectCouncilBranchHandler: ((Int, Int) async throws -> ChatSessionDetail)?
    private let retryCouncilBranchHandler: ((Int, Int) async throws -> ChatSessionDetail)?
    private(set) var initialSuggestionsCallCount = 0
    private(set) var messageStatusCallCount = 0
    private(set) var sentMessages: [(sessionId: Int, message: String)] = []

    init(
        getSessionHandler: ((Int) async throws -> ChatSessionDetail)? = nil,
        sendMessageHandler: ((Int, String) async throws -> SendChatMessageResponse)? = nil,
        messageStatusHandler: ((Int) async throws -> MessageStatusResponse)? = nil,
        selectCouncilBranchHandler: ((Int, Int) async throws -> ChatSessionDetail)? = nil,
        retryCouncilBranchHandler: ((Int, Int) async throws -> ChatSessionDetail)? = nil
    ) {
        self.getSessionHandler = getSessionHandler
        self.sendMessageHandler = sendMessageHandler
        self.messageStatusHandler = messageStatusHandler
        self.selectCouncilBranchHandler = selectCouncilBranchHandler
        self.retryCouncilBranchHandler = retryCouncilBranchHandler
    }

    func getSession(id: Int) async throws -> ChatSessionDetail {
        if let getSessionHandler {
            return try await getSessionHandler(id)
        }
        throw ChatServiceError.timeout
    }

    func sendMessageAsync(sessionId: Int, message: String) async throws -> SendChatMessageResponse {
        sentMessages.append((sessionId: sessionId, message: message))
        if let sendMessageHandler {
            return try await sendMessageHandler(sessionId, message)
        }
        throw ChatServiceError.timeout
    }

    func getMessageStatus(messageId: Int) async throws -> MessageStatusResponse {
        messageStatusCallCount += 1
        if let messageStatusHandler {
            return try await messageStatusHandler(messageId)
        }
        throw ChatServiceError.timeout
    }

    func getInitialSuggestions(sessionId: Int) async throws -> ChatMessage {
        initialSuggestionsCallCount += 1
        throw ChatServiceError.timeout
    }

    func startCouncil(sessionId: Int, message: String) async throws -> ChatSessionDetail {
        throw ChatServiceError.timeout
    }

    func selectCouncilBranch(sessionId: Int, childSessionId: Int) async throws -> ChatSessionDetail {
        if let selectCouncilBranchHandler {
            return try await selectCouncilBranchHandler(sessionId, childSessionId)
        }
        throw ChatServiceError.timeout
    }

    func retryCouncilBranch(sessionId: Int, childSessionId: Int) async throws -> ChatSessionDetail {
        if let retryCouncilBranchHandler {
            return try await retryCouncilBranchHandler(sessionId, childSessionId)
        }
        throw ChatServiceError.timeout
    }

    func updateSessionProvider(sessionId: Int, provider: ChatModelProvider) async throws -> ChatSessionSummary {
        throw ChatServiceError.timeout
    }
}

@MainActor
private final class MockChatSpeechTranscriber: SpeechTranscribing {
    var isAvailable = true
    private(set) var startCallCount = 0
    private(set) var stopCallCount = 0

    private let transcript: String
    private var activeSessionID: UUID?
    private var continuation: AsyncStream<SpeechTranscriptionEvent>.Continuation?

    init(transcript: String) {
        self.transcript = transcript
    }

    func makeSession(
        deadlines: SpeechRecordingDeadlines
    ) throws -> SpeechTranscriptionSession {
        _ = deadlines
        guard activeSessionID == nil else { throw VoiceDictationError.sessionBusy }
        let sessionID = UUID()
        let pair = AsyncStream<SpeechTranscriptionEvent>.makeStream()
        activeSessionID = sessionID
        continuation = pair.continuation
        return SpeechTranscriptionSession(
            id: sessionID,
            events: pair.stream,
            start: { [weak self] id in self?.start(sessionID: id) },
            stop: { [weak self] id in
                guard let self else { throw VoiceDictationError.noActiveSession }
                return try self.stop(sessionID: id)
            },
            cancel: { [weak self] id in self?.cancel(sessionID: id) }
        )
    }

    func emit(_ event: SpeechTranscriptionEvent) {
        continuation?.yield(event)
    }

    func simulateSilenceAutoStop() async {
        await simulateAutomaticStop(reason: .silenceAutoStop)
    }

    func simulateAutomaticStop(reason: SpeechStopReason) async {
        emit(.stateChange(.transcribing))
        emit(.transcriptFinal(transcript))
        emit(.stateChange(.idle))
        emit(.stopReason(reason))
        releaseSession()
        await Task.yield()
    }

    func simulateNoSpeechTimeout() async {
        let message = "No speech detected. Try again."
        emit(.stateChange(.failed(message)))
        emit(.error(message))
        emit(.stopReason(.noSpeechTimeout))
        releaseSession()
        await Task.yield()
    }

    private func start(sessionID: UUID) {
        guard activeSessionID == sessionID else { return }
        startCallCount += 1
        emit(.stateChange(.recording))
    }

    private func stop(sessionID: UUID) throws -> String {
        guard activeSessionID == sessionID else { throw VoiceDictationError.noActiveSession }
        stopCallCount += 1
        releaseSession()
        return transcript
    }

    private func cancel(sessionID: UUID) {
        guard activeSessionID == sessionID else { return }
        emit(.stateChange(.idle))
        emit(.stopReason(.cancel))
        releaseSession()
    }

    private func releaseSession() {
        activeSessionID = nil
        continuation?.finish()
        continuation = nil
    }
}

private actor AsyncGate {
    private var isOpen = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        if isOpen {
            return
        }
        await withCheckedContinuation { continuation in
            waiters.append(continuation)
        }
    }

    func open() {
        isOpen = true
        let continuations = waiters
        waiters.removeAll()
        for continuation in continuations {
            continuation.resume()
        }
    }
}
