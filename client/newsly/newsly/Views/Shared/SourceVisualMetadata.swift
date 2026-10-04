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
    let label: String

    static func sourceType(_ rawValue: String?) -> SourceVisualMetadata {
        switch normalized(rawValue) {
        case "podcast_rss", "podcast":
            return SourceVisualMetadata(glyph: .system("waveform"), label: "Podcast")
        case "youtube":
            return SourceVisualMetadata(glyph: .system("play.rectangle.fill"), label: "YouTube")
        case "substack":
            return SourceVisualMetadata(glyph: .system("newspaper"), label: "Newsletter")
        case "atom", "rss", "feed":
            return SourceVisualMetadata(
                glyph: .system("dot.radiowaves.left.and.right"),
                label: "Feed"
            )
        default:
            return SourceVisualMetadata(glyph: .system("list.bullet.rectangle"), label: "Feed")
        }
    }

    static func platform(_ rawValue: String?) -> SourceVisualMetadata? {
        switch normalized(rawValue) {
        case "":
            return nil
        case "hackernews":
            return SourceVisualMetadata(glyph: .text("Y"), label: "Hacker News")
        case "reddit":
            return SourceVisualMetadata(glyph: .system("arrow.up.circle.fill"), label: "Reddit")
        case "substack":
            return SourceVisualMetadata(glyph: .system("doc.text.fill"), label: "Substack")
        case "podcast", "podcast_rss":
            return SourceVisualMetadata(glyph: .system("mic.fill"), label: "Podcast")
        case "twitter", "x":
            return SourceVisualMetadata(glyph: .system("bird.fill"), label: "X")
        default:
            return SourceVisualMetadata(glyph: .system("link.circle.fill"), label: "Source")
        }
    }

    static func suggestionType(_ rawValue: String?) -> SourceVisualMetadata {
        let value = normalized(rawValue)
        let metadata = sourceType(rawValue)

        switch value {
        case "youtube":
            return SourceVisualMetadata(glyph: .system("play.circle.fill"), label: metadata.label)
        case "feed", "rss":
            return SourceVisualMetadata(
                glyph: .system("dot.radiowaves.up.forward"),
                label: metadata.label
            )
        case "podcast_rss", "podcast", "substack":
            return metadata
        default:
            return SourceVisualMetadata(glyph: .system("doc.text"), label: "Feed")
        }
    }

    private static func normalized(_ rawValue: String?) -> String {
        rawValue?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() ?? ""
    }
}
