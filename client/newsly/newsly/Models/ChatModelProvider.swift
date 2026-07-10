//
//  ChatModelProvider.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import Foundation

/// Available LLM providers for chat sessions
enum ChatModelProvider: String, Codable, CaseIterable {
    case openai
    case anthropic
    case google
    case deep_research

    var displayName: String {
        switch self {
        case .openai:
            return "GPT"
        case .anthropic:
            return "Claude"
        case .google:
            return "Gemini"
        case .deep_research:
            return "Deep Research"
        }
    }

    /// SF Symbol icon name (used for menus that require system images)
    var iconName: String {
        switch self {
        case .openai:
            return "brain.head.profile"
        case .anthropic:
            return "sparkles"
        case .google:
            return "diamond"
        case .deep_research:
            return "magnifyingglass.circle.fill"
        }
    }

    /// Custom asset icon name
    var iconAsset: String {
        switch self {
        case .openai:
            return "openai-icon"
        case .anthropic:
            return "claude-icon"
        case .google:
            return "gemini-icon"
        case .deep_research:
            return "deep-research-icon"
        }
    }

    var chatDisplayName: String {
        switch self {
        case .openai:
            return "GPT-5.5"
        default:
            return displayName
        }
    }

    /// Whether this provider uses deep research (longer processing times)
    var isDeepResearch: Bool {
        self == .deep_research
    }

    /// Short description for the provider
    var tagline: String {
        switch self {
        case .openai:
            return "Fast, versatile reasoning"
        case .anthropic:
            return "Thoughtful, nuanced responses"
        case .google:
            return "Balanced performance"
        case .deep_research:
            return "Comprehensive analysis (2-5 min)"
        }
    }

    /// Accent color for this provider
    var accentColor: String {
        switch self {
        case .openai:
            return "green"
        case .anthropic:
            return "orange"
        case .google:
            return "blue"
        case .deep_research:
            return "purple"
        }
    }

    /// Providers offered for new conversations. Google remains decodable so
    /// existing sessions keep their original provider label.
    static let selectableProviders: [ChatModelProvider] = [.openai, .anthropic]

    /// Providers available for tweet generation.
    static var tweetProviders: [ChatModelProvider] {
        selectableProviders
    }
}

extension ChatModelProvider: Identifiable {
    var id: String { rawValue }
}
