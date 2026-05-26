//
//  ToastService.swift
//  newsly
//
//  Toast notification service for app-wide messaging
//

import Foundation
import SwiftUI

enum ToastType {
    case success
    case error
    case info

    var icon: String {
        switch self {
        case .success: return "checkmark.circle.fill"
        case .error: return "exclamationmark.triangle.fill"
        case .info: return "info.circle.fill"
        }
    }

    var color: Color {
        switch self {
        case .success: return .brandSecondary
        case .error: return .statusDestructive
        case .info: return .brandPrimary
        }
    }
}

struct ToastMessage: Identifiable {
    let id = UUID()
    let message: String
    let type: ToastType
    let duration: TimeInterval

    init(message: String, type: ToastType, duration: TimeInterval = 3.0) {
        self.message = message
        self.type = type
        self.duration = duration
    }
}

@MainActor
class ToastService: ObservableObject {
    static let shared = ToastService()

    @Published var currentToast: ToastMessage?
    private var dismissTask: Task<Void, Never>?

    private init() {}

    func show(_ message: String, type: ToastType = .info, duration: TimeInterval = 3.0) {
        dismissTask?.cancel()  // Cancel any existing dismiss task

        let newToast = ToastMessage(message: message, type: type, duration: duration)
        currentToast = newToast

        dismissTask = Task {
            try? await Task.sleep(nanoseconds: UInt64(duration * 1_000_000_000))
            if currentToast?.id == newToast.id {
                currentToast = nil
            }
            dismissTask = nil
        }
    }

    func showError(_ message: String) {
        show(message, type: .error)
    }

    func showSuccess(_ message: String) {
        show(message, type: .success)
    }
}
