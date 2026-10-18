import Foundation

/// News decks belong to the converted article, whose URL is retained in the deck.
/// Compare the same HTTPS source URL used by the server's news-deck conversion.
enum BriefingSourceDeckLookup {
    static func find(
        source: KnowledgeSource,
        sourceURL: String?,
        decks: [LearningDeck]
    ) -> LearningDeck? {
        switch source {
        case .content:
            return decks.first { $0.knowledgeSource == source }
        case .news:
            guard let url = normalizedNewsURL(sourceURL) else { return nil }
            return decks.first {
                $0.sourceKind == .content && normalizedNewsURL($0.sourceURL) == url
            }
        }
    }

    static func normalizedNewsURL(_ raw: String?) -> String? {
        guard let raw else { return nil }
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let candidate: String
        if trimmed.hasPrefix("//") {
            candidate = "https:" + trimmed
        } else if URLComponents(string: trimmed)?.scheme != nil {
            candidate = trimmed
        } else {
            candidate = "https://" + trimmed
        }
        guard var components = URLComponents(string: candidate),
              let scheme = components.scheme?.lowercased(),
              ["http", "https"].contains(scheme),
              let host = components.host, !host.isEmpty
        else { return nil }
        // URL's parser removes the original scheme's default port before the
        // backend sets HTTPS; retain any explicitly non-default port.
        if components.port == (scheme == "http" ? 80 : 443) {
            components.port = nil
        }
        components.scheme = "https"
        if components.port == 443 { components.port = nil }
        components.host = host.lowercased()
        if components.path.isEmpty { components.path = "/" }
        return components.url?.standardized.absoluteString
    }
}
