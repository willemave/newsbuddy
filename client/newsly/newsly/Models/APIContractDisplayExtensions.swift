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
        }
    }
}

extension APIContentStatus {
    var displayName: String {
        switch self {
        case .new:
            "New"
        case .pending:
            "Pending"
        case .processing:
            "Processing"
        case .awaiting_image:
            "Awaiting Image"
        case .completed:
            "Completed"
        case .failed:
            "Failed"
        case .skipped:
            "Skipped"
        }
    }
}
