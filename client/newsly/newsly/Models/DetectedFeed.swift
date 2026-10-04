import Foundation

typealias DetectedFeed = APIDetectedFeed

extension APIDetectedFeed {
    var feedTypeName: String {
        switch type {
        case "substack": "Substack"
        case "podcast_rss": "Podcast"
        case "atom": "RSS Feed"
        default: "Feed"
        }
    }

    var systemIcon: String {
        switch type {
        case "substack": "envelope.fill"
        case "podcast_rss": "mic.fill"
        case "atom": "dot.radiowaves.left.and.right"
        default: "antenna.radiowaves.left.and.right"
        }
    }
}
