//
//  ActiveChatSessionManager.swift
//  newsly
//
//  Created by Assistant on 12/6/25.
//

import Foundation
import SwiftUI
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ActiveChatSessionManager")

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
class ActiveChatSessionManager: ObservableObject {
    static let shared = ActiveChatSessionManager()

    /// Active sessions being polled, keyed by session ID
    @Published private(set) var activeSessions: [Int: ActiveChatSession] = [:]  // sessionId -> session

    /// Completed sessions that haven't been viewed yet, keyed by session ID
    @Published private(set) var completedSessions: [Int: ActiveChatSession] = [:]  // sessionId -> session

    private let chatService: any ChatSessionServicing
    private let notificationService = LocalNotificationService.shared
    private let startsPolling: Bool

    /// Polling interval (500ms)
    private let pollingInterval: UInt64 = 500_000_000

    /// Maximum polling attempts (120 = 60 seconds)
    private let maxPollingAttempts = 120

    private var pollingTasks: [Int: Task<Void, Never>] = [:]  // sessionId -> task
    private var sessionIdsByItemKey: [String: [Int]] = [:]  // item key -> newest-first session IDs
    private var authDidLogOutObserver: NSObjectProtocol?

    init(
        chatService: any ChatSessionServicing = ChatService.shared,
        startsPolling: Bool = true
    ) {
        self.chatService = chatService
        self.startsPolling = startsPolling
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

        guard startsPolling else { return }

        // Start background polling
        let task = Task {
            await pollForCompletion(sessionId: session.id, messageId: messageId)
        }
        pollingTasks[session.id] = task
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

    /// Poll for message completion
    private func pollForCompletion(sessionId: Int, messageId: Int) async {
        var attempts = 0

        while attempts < maxPollingAttempts {
            do {
                try Task.checkCancellation()

                let status = try await chatService.getMessageStatus(messageId: messageId)

                switch status.status {
                case .completed:
                    await handleCompletion(sessionId: sessionId)
                    return

                case .failed:
                    let errorMsg = status.error ?? "Unknown error"
                    await handleFailure(sessionId: sessionId, error: errorMsg)
                    return

                case .processing, .unknown(_):
                    attempts += 1
                    try await Task.sleep(nanoseconds: pollingInterval)
                }
            } catch is CancellationError {
                logger.info("Polling cancelled for session \(sessionId)")
                return
            } catch {
                logger.error("Polling error for session \(sessionId): \(error.localizedDescription)")
                await handleFailure(sessionId: sessionId, error: error.localizedDescription)
                return
            }
        }

        // Timeout
        await handleFailure(sessionId: sessionId, error: "Request timed out")
    }

    private func handleCompletion(sessionId: Int) async {
        guard var session = activeSessions[sessionId] else { return }

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

    private func handleFailure(sessionId: Int, error: String) async {
        guard var session = activeSessions[sessionId] else { return }

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
