//
//  LocalNotificationService.swift
//  newsly
//
//  Created by Assistant on 12/6/25.
//

import Foundation
import Observation
import UserNotifications
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "LocalNotificationService")

/// Service for handling local push notifications
@MainActor
@Observable
final class LocalNotificationService: NSObject {
    static let shared = LocalNotificationService()

    private(set) var isAuthorized = false

    @ObservationIgnored
    private let notificationCenter = UNUserNotificationCenter.current()

    private override init() {
        super.init()
        notificationCenter.delegate = self
    }

    /// Request notification permissions
    func requestAuthorization() async {
        do {
            let granted = try await notificationCenter.requestAuthorization(options: [.alert, .sound, .badge])
            isAuthorized = granted
            logger.info("Notification authorization: \(granted)")
        } catch {
            logger.error("Failed to request notification authorization: \(error.localizedDescription)")
            isAuthorized = false
        }
    }

    /// Check current authorization status
    func checkAuthorizationStatus() async {
        let settings = await notificationCenter.notificationSettings()
        isAuthorized = settings.authorizationStatus == .authorized
    }

    /// Show an in-app notification when a chat session completes
    func showChatCompletedNotification(sessionId: Int, title: String, message: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = message
        content.sound = .default
        content.userInfo = [
            "type": "chat_completed",
            "sessionId": sessionId
        ]

        // Deliver immediately (with minimal delay for in-app)
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 0.1, repeats: false)
        let request = UNNotificationRequest(
            identifier: "chat_completed_\(sessionId)",
            content: content,
            trigger: trigger
        )

        notificationCenter.add(request) { error in
            if let error = error {
                logger.error("Failed to schedule notification: \(error.localizedDescription)")
            } else {
                logger.info("Scheduled chat completion notification for session \(sessionId)")
            }
        }
    }

    /// Remove pending notification for a session
    func removePendingNotification(sessionId: Int) {
        notificationCenter.removePendingNotificationRequests(
            withIdentifiers: ["chat_completed_\(sessionId)"]
        )
    }
}

// MARK: - UNUserNotificationCenterDelegate
extension LocalNotificationService: UNUserNotificationCenterDelegate {
    /// Handle notifications when app is in foreground - show as banner
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        return [.banner, .sound]
    }

    /// Handle notification tap
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let userInfo = response.notification.request.content.userInfo

        if let type = userInfo["type"] as? String,
           type == "chat_completed",
           let sessionId = userInfo["sessionId"] as? Int {
            logger.info("User tapped chat completion notification for session \(sessionId)")
            await MainActor.run {
                ChatNavigationCoordinator.shared.open(ChatSessionRoute(sessionId: sessionId))
            }
        }
    }
}
