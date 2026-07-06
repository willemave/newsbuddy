//
//  MarkAllTarget.swift
//  newsly
//

enum MarkAllTarget: String, CaseIterable {
    case article
    case podcast
    case news

    var singularLabel: String {
        switch self {
        case .article: return "Article"
        case .podcast: return "Podcast"
        case .news: return "News item"
        }
    }

    var pluralLabel: String {
        switch self {
        case .article: return "Articles"
        case .podcast: return "Podcasts"
        case .news: return "News items"
        }
    }

    var buttonTitle: String {
        "Mark all \(pluralLabel.lowercased()) as read"
    }

    func description(for count: Int) -> String {
        count == 1 ? singularLabel.lowercased() : pluralLabel.lowercased()
    }
}
