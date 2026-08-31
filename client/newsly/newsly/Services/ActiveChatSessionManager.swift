//
//  ActiveChatSessionManager.swift
//  newsly
//
//  Created by Assistant on 12/6/25.
//

import Foundation
import Observation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ActiveChatSessionManager")

@MainActor
protocol ChatCompletionNotifying: AnyObject {
    func showChatCompletedNotification(sessionId: Int, title: String, message: String)
}

@MainActor
private final class NoopChatCompletionNotifier: ChatCompletionNotifying {
    func showChatCompletedNotification(sessionId: Int, title: String, message: String) {}
}

private final class UnavailableMessageStatusService: MessageStatusFetching {
    func getMessageStatus(messageId: Int) async throws -> MessageStatusResponse {
        throw CancellationError()
    }
}

/// Represents an active chat session being polled in the background
struct ActiveChatSession: Identifiable, Equatable {
    let id: Int  // session ID
    let contentId: Int?
    let newsItemId: Int?
    let contentTitle: String
    let messageId: Int
    var status: ActiveChatStatus

    var itemKey: String? {
        if let contentId {
            return "content:\(contentId)"
        }
        if let newsItemId {
            return "news:\(newsItemId)"
        }
        return nil
    }

    enum ActiveChatStatus: Equatable {
        case processing
        case completed
        case failed(String)
    }
}

/// Manager for tracking and polling active chat sessions in the background
@MainActor
@Observable
final class ActiveChatSessionManager {
    /// Active sessions being polled, keyed by session ID
    private(set) var activeSessions: [Int: ActiveChatSession] = [:]  // sessionId -> session

    /// Completed sessions that haven't been viewed yet, keyed by session ID
    private(set) var completedSessions: [Int: ActiveChatSession] = [:]  // sessionId -> session

    @ObservationIgnored
    let messageCompletionRegistry: ChatMessageCompletionRegistry

    @ObservationIgnored
    private let notificationService: any ChatCompletionNotifying

    @ObservationIgnored
    private let startsPolling: Bool

    @ObservationIgnored
    private var pollingTasks: [Int: Task<Void, Never>] = [:]  // sessionId -> task

    @ObservationIgnored
    private var isPollingSuspended = false

    @ObservationIgnored
    private var sessionIdsByItemKey: [String: [Int]] = [:]  // item key -> newest-first session IDs

    @ObservationIgnored
    private var authDidLogOutObserver: NSObjectProtocol?

    init(
        messageCompletionRegistry: ChatMessageCompletionRegistry = ChatMessageCompletionRegistry(
            statusService: UnavailableMessageStatusService()
        ),
        notificationService: (any ChatCompletionNotifying)? = nil,
        startsPolling: Bool = true,
        observesAuthenticationNotifications: Bool = true
    ) {
        self.messageCompletionRegistry = messageCompletionRegistry
        self.notificationService = notificationService ?? NoopChatCompletionNotifier()
        self.startsPolling = startsPolling
        if observesAuthenticationNotifications {
            authDidLogOutObserver = NotificationCenter.default.addObserver(
                forName: .authDidLogOut,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor in
                    self?.reset()
                }
            }
        }
    }

    deinit {
        if let authDidLogOutObserver {
            NotificationCenter.default.removeObserver(authDidLogOutObserver)
        }
    }

    /// Start tracking a new chat session
    func startTracking(
        session: ChatSessionSummary,
        contentId: Int? = nil,
        newsItemId: Int? = nil,
        contentTitle: String,
        messageId: Int
    ) {
        if let existing = activeSessions[session.id], existing.messageId == messageId {
            logger.info("Already tracking session \(session.id) for message \(messageId)")
            return
        }

        pollingTasks[session.id]?.cancel()
        pollingTasks.removeValue(forKey: session.id)

        let activeSession = ActiveChatSession(
            id: session.id,
            contentId: contentId,
            newsItemId: newsItemId,
            contentTitle: contentTitle,
            messageId: messageId,
            status: .processing
        )

        activeSessions[session.id] = activeSession
        insertSessionReference(sessionId: session.id, itemKey: activeSession.itemKey)
        logger.info("Started tracking session \(session.id)")

        startPollingIfNeeded(sessionId: session.id, messageId: messageId)
    }

    /// Stop tracking a session (e.g., when user opens the chat view)
    func stopTracking(sessionId: Int) {
        pollingTasks[sessionId]?.cancel()
        pollingTasks.removeValue(forKey: sessionId)

        let session = activeSessions.removeValue(forKey: sessionId) ?? completedSessions.removeValue(forKey: sessionId)
        if let session {
            removeSessionReference(sessionId: sessionId, itemKey: session.itemKey)
            logger.info("Stopped tracking session \(sessionId)")
        } else {
            logger.info("Stopped tracking session \(sessionId)")
        }
    }

    func reset() {
        for task in pollingTasks.values {
            task.cancel()
        }
        pollingTasks.removeAll()
        activeSessions.removeAll()
        completedSessions.removeAll()
        sessionIdsByItemKey.removeAll()
        logger.info("Reset all active chat tracking state")
    }

    func setPollingSuspended(_ isSuspended: Bool) {
        guard isPollingSuspended != isSuspended else { return }
        isPollingSuspended = isSuspended

        if isSuspended {
            for task in pollingTasks.values {
                task.cancel()
            }
            pollingTasks.removeAll()
            logger.info("Suspended active chat session polling")
        } else {
            restartPollingForActiveSessions()
            logger.info("Resumed active chat session polling")
        }
    }

    /// Mark a completed session as viewed (dismisses banner)
    func markAsViewed(sessionId: Int) {
        guard let session = completedSessions.removeValue(forKey: sessionId) else { return }
        removeSessionReference(sessionId: sessionId, itemKey: session.itemKey)
    }

    /// Get active session for a content ID if any
    func getSession(forContentId contentId: Int) -> ActiveChatSession? {
        getSession(forItemKey: "content:\(contentId)")
    }

    func getSession(forNewsItemId newsItemId: Int) -> ActiveChatSession? {
        getSession(forItemKey: "news:\(newsItemId)")
    }

    private func getSession(forItemKey itemKey: String) -> ActiveChatSession? {
        let sessionIds = sessionIdsByItemKey[itemKey] ?? []

        for sessionId in sessionIds {
            if let session = activeSessions[sessionId] {
                return session
            }
        }

        for sessionId in sessionIds {
            if let session = completedSessions[sessionId] {
                return session
            }
        }

        return nil
    }

    /// Check if there's an active or completed session for this content
    func hasActiveSession(forContentId contentId: Int) -> Bool {
        getSession(forContentId: contentId) != nil
    }

    /// Number of sessions currently processing (for tab badge)
    var processingCount: Int {
        activeSessions.count
    }

    /// Whether any sessions are currently processing
    var hasProcessingSessions: Bool {
        !activeSessions.isEmpty
    }

    private func startPollingIfNeeded(sessionId: Int, messageId: Int) {
        guard startsPolling, !isPollingSuspended, pollingTasks[sessionId] == nil else { return }

        let task = Task {
            await pollForCompletion(sessionId: sessionId, messageId: messageId)
        }
        pollingTasks[sessionId] = task
    }

    private func restartPollingForActiveSessions() {
        guard startsPolling, !isPollingSuspended else { return }

        for (sessionId, session) in activeSessions {
            startPollingIfNeeded(sessionId: sessionId, messageId: session.messageId)
        }
    }

    /// Poll for message completion
    private func pollForCompletion(sessionId: Int, messageId: Int) async {
        do {
            _ = try await messageCompletionRegistry.waitForCompletion(messageId: messageId)
            handleCompletion(sessionId: sessionId, messageId: messageId)
        } catch where ClientFailure.classify(error) == .cancelled {
            logger.info("Polling cancelled for session \(sessionId)")
        } catch ChatServiceError.processingFailed(let message) {
            handleFailure(sessionId: sessionId, messageId: messageId, error: message)
        } catch ChatServiceError.timeout {
            handleFailure(
                sessionId: sessionId,
                messageId: messageId,
                error: "Request timed out"
            )
        } catch {
            logger.error("Polling error for session \(sessionId): \(error.localizedDescription)")
            handleFailure(
                sessionId: sessionId,
                messageId: messageId,
                error: error.localizedDescription
            )
        }
    }

    func handleCompletion(sessionId: Int, messageId: Int) {
        guard var session = activeSessions[sessionId],
              session.messageId == messageId else {
            return
        }

        session.status = .completed
        activeSessions.removeValue(forKey: sessionId)
        completedSessions[sessionId] = session
        pollingTasks.removeValue(forKey: sessionId)

        logger.info("Chat completed for session \(sessionId)")

        // Show local notification
        notificationService.showChatCompletedNotification(
            sessionId: session.id,
            title: "Chat Ready",
            message: "Your analysis of \"\(session.contentTitle)\" is ready"
        )
    }

    func handleFailure(sessionId: Int, messageId: Int, error: String) {
        guard var session = activeSessions[sessionId],
              session.messageId == messageId else {
            return
        }

        session.status = .failed(error)
        activeSessions.removeValue(forKey: sessionId)
        completedSessions[sessionId] = session
        pollingTasks.removeValue(forKey: sessionId)

        logger.error("Chat failed for session \(sessionId): \(error)")
    }

    private func insertSessionReference(sessionId: Int, itemKey: String?) {
        guard let itemKey else { return }
        var sessionIds = sessionIdsByItemKey[itemKey] ?? []
        sessionIds.removeAll { $0 == sessionId }
        sessionIds.insert(sessionId, at: 0)
        sessionIdsByItemKey[itemKey] = sessionIds
    }

    private func removeSessionReference(sessionId: Int, itemKey: String?) {
        guard let itemKey, var sessionIds = sessionIdsByItemKey[itemKey] else { return }
        sessionIds.removeAll { $0 == sessionId }

        if sessionIds.isEmpty {
            sessionIdsByItemKey.removeValue(forKey: itemKey)
        } else {
            sessionIdsByItemKey[itemKey] = sessionIds
        }
    }
}
