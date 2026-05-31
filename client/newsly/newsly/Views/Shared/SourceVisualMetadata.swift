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
            return SourceVisualMetadata(glyph: .system("waveform"), color: .onSurfaceSecondary, label: "Podcast")
        case "youtube":
            return SourceVisualMetadata(glyph: .system("play.rectangle.fill"), color: .statusDestructive, label: "YouTube")
        case "substack":
            return SourceVisualMetadata(glyph: .system("newspaper"), color: .onSurfaceSecondary, label: "Newsletter")
        case "atom", "rss", "feed":
            return SourceVisualMetadata(
                glyph: .system("dot.radiowaves.left.and.right"),
                color: .onSurfaceSecondary,
                label: "Feed"
            )
        default:
            return SourceVisualMetadata(glyph: .system("list.bullet.rectangle"), color: .onSurfaceSecondary, label: "Feed")
        }
    }

    static func platform(_ rawValue: String?) -> SourceVisualMetadata? {
        switch normalized(rawValue) {
        case "":
            return nil
        case "hackernews":
            return SourceVisualMetadata(glyph: .text("Y"), color: .onSurfaceSecondary, label: "Hacker News")
        case "reddit":
            return SourceVisualMetadata(glyph: .system("arrow.up.circle.fill"), color: .onSurfaceSecondary, label: "Reddit")
        case "substack":
            return SourceVisualMetadata(glyph: .system("doc.text.fill"), color: .onSurfaceSecondary, label: "Substack")
        case "podcast", "podcast_rss":
            return SourceVisualMetadata(glyph: .system("mic.fill"), color: .onSurfaceSecondary, label: "Podcast")
        case "twitter", "x":
            return SourceVisualMetadata(glyph: .system("bird.fill"), color: .onSurfaceSecondary, label: "X")
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
            return SourceVisualMetadata(glyph: .system("doc.text"), color: .onSurfaceSecondary, label: "Feed")
        }
    }

    private static func normalized(_ rawValue: String?) -> String {
        rawValue?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() ?? ""
    }
}
