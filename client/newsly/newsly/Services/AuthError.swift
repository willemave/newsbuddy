//
//  AuthError.swift
//  newsly
//

import Foundation

enum AuthError: Error, LocalizedError {
    case notAuthenticated
    case noRefreshToken
    case refreshTokenExpired
    case refreshFailed
    case serverError(statusCode: Int, message: String?)
    case networkError(Error)
    case appleSignInFailed

    var errorDescription: String? {
        switch self {
        case .notAuthenticated:
            return "Not authenticated"
        case .noRefreshToken:
            return "No refresh token available"
        case .refreshTokenExpired:
            return "Refresh token expired"
        case .refreshFailed:
            return "Failed to refresh token"
        case .serverError(let statusCode, let message):
            return "Server error \(statusCode): \(message ?? "Unknown")"
        case .networkError(let error):
            return "Network error: \(error.localizedDescription)"
        case .appleSignInFailed:
            return "Apple Sign In failed"
        }
    }

    var userFacingMessage: String {
        switch self {
        case .notAuthenticated, .noRefreshToken, .refreshTokenExpired:
            return "Your session expired. Sign in again to continue."
        case .refreshFailed:
            return "We couldn't restore your session. Please try again."
        case .serverError(let statusCode, let message):
            return Self.sanitizedServerMessage(statusCode: statusCode, message: message)
        case .networkError:
            return "We couldn't reach Newsbuddy. Check your connection and try again."
        case .appleSignInFailed:
            return "Apple Sign In couldn't be completed. Please try again."
        }
    }

    private static func sanitizedServerMessage(statusCode: Int, message: String?) -> String {
        guard let extractedMessage = extractedMessage(from: message) else {
            return fallbackMessage(for: statusCode)
        }

        if looksLikeHTML(extractedMessage) || statusCode >= 500 {
            return fallbackMessage(for: statusCode)
        }

        return extractedMessage
    }

    private static func extractedMessage(from rawMessage: String?) -> String? {
        guard let rawMessage else { return nil }

        let trimmed = rawMessage.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }

        if let jsonMessage = jsonFieldMessage(from: trimmed) {
            return jsonMessage
        }

        return trimmed
    }

    private static func jsonFieldMessage(from rawMessage: String) -> String? {
        guard let data = rawMessage.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            return nil
        }

        for key in ["detail", "message", "error", "error_message"] {
            guard let value = object[key] as? String else { continue }
            let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty {
                return trimmed
            }
        }

        return nil
    }

    private static func fallbackMessage(for statusCode: Int) -> String {
        switch statusCode {
        case 429:
            return "Too many attempts right now. Please try again in a moment."
        case 500...599:
            return "Newsbuddy is temporarily unavailable. Please try again in a moment."
        default:
            return "Something went wrong. Please try again."
        }
    }

    private static func looksLikeHTML(_ message: String) -> Bool {
        let lowercaseMessage = message.lowercased()
        let htmlIndicators = [
            "<!doctype",
            "<html",
            "<head",
            "<body",
            "</html",
            "</body",
            "<title",
            "<meta",
            "<div",
            "<span",
            "text/html",
        ]

        return htmlIndicators.contains { lowercaseMessage.contains($0) }
    }
}
