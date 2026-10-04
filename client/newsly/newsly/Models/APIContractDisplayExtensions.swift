import Foundation

extension APIContentType {
    var displayName: String {
        switch self {
        case .article:
            "Article"
        case .podcast:
            "Podcast"
        case .news:
            "News"
        case .insight_report:
            "Insight Report"
        case .unknown:
            "Unknown"
        case .unknownRaw:
            "Unknown"
        }
    }
}
