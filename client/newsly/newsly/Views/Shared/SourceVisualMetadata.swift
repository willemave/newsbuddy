//
//  SourceVisualMetadata.swift
//  newsly
//
//  Shared icon, label, and color policy for source and platform types.
//

import SwiftUI

struct SourceVisualMetadata {
    enum Glyph {
        case system(String)
        case text(String)
    }

    let glyph: Glyph
    let color: Color
    let label: String

    var systemImageName: String {
        switch glyph {
        case .system(let name):
            return name
        case .text:
            return "link.circle.fill"
        }
    }

    static func sourceType(_ rawValue: String?) -> SourceVisualMetadata {
        switch normalized(rawValue) {
        case "podcast_rss", "podcast":
            return SourceVisualMetadata(glyph: .system("waveform"), color: .brandTertiary, label: "Podcast")
        case "youtube":
            return SourceVisualMetadata(glyph: .system("play.rectangle.fill"), color: .statusDestructive, label: "YouTube")
        case "substack":
            return SourceVisualMetadata(glyph: .system("newspaper"), color: .brandPrimary, label: "Newsletter")
        case "atom", "rss", "feed":
            return SourceVisualMetadata(
                glyph: .system("dot.radiowaves.left.and.right"),
                color: .brandSecondary,
                label: "Feed"
            )
        default:
            return SourceVisualMetadata(glyph: .system("list.bullet.rectangle"), color: .brandSecondary, label: "Feed")
        }
    }

    static func platform(_ rawValue: String?) -> SourceVisualMetadata? {
        switch normalized(rawValue) {
        case "":
            return nil
        case "hackernews":
            return SourceVisualMetadata(glyph: .text("Y"), color: .brandPrimary, label: "Hacker News")
        case "reddit":
            return SourceVisualMetadata(glyph: .system("arrow.up.circle.fill"), color: .brandTertiary, label: "Reddit")
        case "substack":
            return SourceVisualMetadata(glyph: .system("doc.text.fill"), color: .brandPrimary, label: "Substack")
        case "podcast", "podcast_rss":
            return SourceVisualMetadata(glyph: .system("mic.fill"), color: .brandTertiary, label: "Podcast")
        case "twitter", "x":
            return SourceVisualMetadata(glyph: .system("bird.fill"), color: .brandSecondary, label: "X")
        default:
            return SourceVisualMetadata(glyph: .system("link.circle.fill"), color: .onSurfaceSecondary, label: "Source")
        }
    }

    static func suggestionType(_ rawValue: String?) -> SourceVisualMetadata {
        let value = normalized(rawValue)
        let metadata = sourceType(rawValue)

        switch value {
        case "youtube":
            return SourceVisualMetadata(glyph: .system("play.circle.fill"), color: metadata.color, label: metadata.label)
        case "feed", "rss":
            return SourceVisualMetadata(
                glyph: .system("dot.radiowaves.up.forward"),
                color: metadata.color,
                label: metadata.label
            )
        case "podcast_rss", "podcast", "substack":
            return metadata
        default:
            return SourceVisualMetadata(glyph: .system("doc.text"), color: .brandSecondary, label: "Feed")
        }
    }

    private static func normalized(_ rawValue: String?) -> String {
        rawValue?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() ?? ""
    }
}
