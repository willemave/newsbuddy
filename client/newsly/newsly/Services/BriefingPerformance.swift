import OSLog

enum BriefingPerformance {
    static let signposter = OSSignposter(
        subsystem: "com.newsly.briefing",
        category: "performance"
    )
    static let logger = Logger(
        subsystem: "com.newsly",
        category: "BriefingPerformance"
    )
}
