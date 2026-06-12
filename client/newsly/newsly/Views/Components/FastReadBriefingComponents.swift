//
//  FastReadBriefingComponents.swift
//  newsly
//
//  Components and display helpers for the Fast Read surface.
//

import Foundation
import SwiftUI

enum FastReadPresentation {
    static func sourceLabel(for item: ContentSummary) -> String? {
        if let platform = normalizedText(item.platform) {
            return platform.uppercased()
        }
        if let source = normalizedText(item.source) {
            return source.uppercased()
        }
        return nil
    }

    private static func normalizedText(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

struct ShortNewsQuickAction: Identifiable {
    let id: String
    let title: String
    let systemImage: String
    let prompt: String
    let screenContext: AssistantScreenContext
}
