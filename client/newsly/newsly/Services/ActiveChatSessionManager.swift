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
    let contentId: Int
    let contentTitle: String
    let messageId: Int
    var status: ActiveChatStatus

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

    private let chatService = ChatService.shared
    private let notificationService = LocalNotificationService.shared

    /// Polling interval (500ms)
    private let pollingInterval: UInt64 = 500_000_000

    /// Maximum polling attempts (120 = 60 seconds)
    private let maxPollingAttempts = 120

    private var pollingTasks: [Int: Task<Void, Never>] = [:]  // sessionId -> task
    private var sessionIdsByContentId: [Int: [Int]] = [:]  // contentId -> newest-first session IDs
    private var authDidLogOutObserver: NSObjectProtocol?

    private init() {
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
        contentId: Int,
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
            contentTitle: contentTitle,
            messageId: messageId,
            status: .processing
        )

        activeSessions[session.id] = activeSession
        insertSessionReference(sessionId: session.id, contentId: contentId)
        logger.info("Started tracking session \(session.id) for content \(contentId)")

        // Start background polling
        let task = Task {
            await pollForCompletion(sessionId: session.id, contentId: contentId, messageId: messageId)
        }
        pollingTasks[session.id] = task
    }

    /// Stop tracking a session (e.g., when user opens the chat view)
    func stopTracking(sessionId: Int) {
        pollingTasks[sessionId]?.cancel()
        pollingTasks.removeValue(forKey: sessionId)

        let session = activeSessions.removeValue(forKey: sessionId) ?? completedSessions.removeValue(forKey: sessionId)
        if let session {
            removeSessionReference(sessionId: sessionId, contentId: session.contentId)
            logger.info("Stopped tracking session \(sessionId) for content \(session.contentId)")
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
        sessionIdsByContentId.removeAll()
        logger.info("Reset all active chat tracking state")
    }

    /// Mark a completed session as viewed (dismisses banner)
    func markAsViewed(sessionId: Int) {
        guard let session = completedSessions.removeValue(forKey: sessionId) else { return }
        removeSessionReference(sessionId: sessionId, contentId: session.contentId)
    }

    /// Get active session for a content ID if any
    func getSession(forContentId contentId: Int) -> ActiveChatSession? {
        let sessionIds = sessionIdsByContentId[contentId] ?? []

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
    private func pollForCompletion(sessionId: Int, contentId: Int, messageId: Int) async {
        var attempts = 0

        while attempts < maxPollingAttempts {
            do {
                try Task.checkCancellation()

                let status = try await chatService.getMessageStatus(messageId: messageId)

                switch status.status {
                case .completed:
                    await handleCompletion(sessionId: sessionId, contentId: contentId)
                    return

                case .failed:
                    let errorMsg = status.error ?? "Unknown error"
                    await handleFailure(sessionId: sessionId, contentId: contentId, error: errorMsg)
                    return

                case .processing:
                    attempts += 1
                    try await Task.sleep(nanoseconds: pollingInterval)
                }
            } catch is CancellationError {
                logger.info("Polling cancelled for content \(contentId)")
                return
            } catch {
                logger.error("Polling error for content \(contentId): \(error.localizedDescription)")
                await handleFailure(sessionId: sessionId, contentId: contentId, error: error.localizedDescription)
                return
            }
        }

        // Timeout
        await handleFailure(sessionId: sessionId, contentId: contentId, error: "Request timed out")
    }

    private func handleCompletion(sessionId: Int, contentId: Int) async {
        guard var session = activeSessions[sessionId] else { return }

        session.status = .completed
        activeSessions.removeValue(forKey: sessionId)
        completedSessions[sessionId] = session
        pollingTasks.removeValue(forKey: sessionId)

        logger.info("Chat completed for content \(contentId)")

        // Show local notification
        notificationService.showChatCompletedNotification(
            sessionId: session.id,
            title: "Chat Ready",
            message: "Your analysis of \"\(session.contentTitle)\" is ready"
        )
    }

    private func handleFailure(sessionId: Int, contentId: Int, error: String) async {
        guard var session = activeSessions[sessionId] else { return }

        session.status = .failed(error)
        activeSessions.removeValue(forKey: sessionId)
        completedSessions[sessionId] = session
        pollingTasks.removeValue(forKey: sessionId)

        logger.error("Chat failed for content \(contentId): \(error)")
    }

    private func insertSessionReference(sessionId: Int, contentId: Int) {
        var sessionIds = sessionIdsByContentId[contentId] ?? []
        sessionIds.removeAll { $0 == sessionId }
        sessionIds.insert(sessionId, at: 0)
        sessionIdsByContentId[contentId] = sessionIds
    }

    private func removeSessionReference(sessionId: Int, contentId: Int) {
        guard var sessionIds = sessionIdsByContentId[contentId] else { return }
        sessionIds.removeAll { $0 == sessionId }

        if sessionIds.isEmpty {
            sessionIdsByContentId.removeValue(forKey: contentId)
        } else {
            sessionIdsByContentId[contentId] = sessionIds
        }
    }
}
