import Foundation

/// An article or podcast segment covers one full work, so the Articles and
/// Podcasts lenses present it as a card: hero image, publisher and title over
/// the image, a length chip, the composed summary, and a date footer.
/// News roundups are not cards.
struct BriefingSourceCard: Equatable {
    enum Kind: Equatable {
        case article
        case podcast

        var label: String {
            switch self {
            case .article: "Article"
            case .podcast: "Podcast"
            }
        }
    }

    let contentId: Int
    let sourceKey: String
    let kind: Kind
    let title: String
    let publisher: String?
    /// "12 min read" or "1 hr 6 min"; nil when the length is unknown.
    let length: String?
    let imageURL: URL?
    let thumbnailURL: URL?
    let briefedAt: Date
    let publishedAt: Date?

    init?(segment: APIBriefingSegment, sourcesByKey: [String: APIBriefingSource]) {
        guard segment.sourceKeys.count == 1,
              let source = sourcesByKey[segment.sourceKeys[0]],
              source.kind == "content"
        else { return nil }
        let title = source.title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return nil }

        switch source.contentType {
        case .article:
            kind = .article
            length = source.readingMinutes.flatMap { $0 > 0 ? "\($0) min read" : nil }
        case .podcast:
            kind = .podcast
            length = source.durationSeconds.flatMap(Self.durationLabel)
        default:
            return nil
        }
        self.contentId = source.id
        self.sourceKey = source.sourceKey
        self.title = title
        self.publisher = source.publisher.flatMap(Self.publisherName)
        self.imageURL = ServerImageURL.resolve(source.imageUrl)
        self.thumbnailURL = ServerImageURL.resolve(source.thumbnailUrl)
        self.briefedAt = segment.createdAt
        self.publishedAt = source.publishedAt
    }

    /// Card summaries read smaller than News prose so the card stays led by its image and
    /// title; reader text-size settings still scale it.
    static let summaryStyle = BriefingAttributedTextBuilder.BodyStyle(
        size: 13,
        color: .appReaderBodyText
    )

    var publicationDate: String? {
        publishedAt.map { Self.shortDate($0) }
    }

    /// Footer metadata. Length lives on the image chip, so the footer carries
    /// only the original publication date, falling back to the kind so the
    /// row is never empty. Unknown dates never substitute the Briefed time.
    var footerLine: String {
        publicationDate ?? kind.label
    }

    var briefedLabel: String {
        "Briefed \(briefedAt.formatted(.dateTime.month(.abbreviated).day().year().hour().minute()))"
    }

    /// Everything the card shows visually, plus the Briefed time, in one label.
    var accessibilityLabel: String {
        [
            title,
            publisher,
            [kind.label, length].compactMap { $0 }.joined(separator: ", "),
            publicationDate.map { "Published \($0)" },
            briefedLabel,
        ]
        .compactMap { $0 }
        .joined(separator: ", ")
    }

    /// Feed titles often append a tagline ("Show — Interviews", "Blog | Network");
    /// the kicker keeps only the name.
    static func publisherName(_ raw: String) -> String? {
        let name = [" | ", " — ", " – "].reduce(raw) { name, separator in
            name.components(separatedBy: separator).first ?? name
        }
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    /// "Oct 16" within the current year, "Oct 16, 2023" otherwise.
    static func shortDate(_ date: Date, now: Date = .now, calendar: Calendar = .current) -> String {
        guard calendar.isDate(date, equalTo: now, toGranularity: .year) else {
            return date.formatted(.dateTime.month(.abbreviated).day().year())
        }
        return date.formatted(.dateTime.month(.abbreviated).day())
    }

    static func durationLabel(seconds: Int) -> String? {
        guard seconds > 0 else { return nil }
        let minutes = max(Int((Double(seconds) / 60).rounded()), 1)
        guard minutes >= 60 else { return "\(minutes) min" }
        let remainder = minutes % 60
        return remainder == 0 ? "\(minutes / 60) hr" : "\(minutes / 60) hr \(remainder) min"
    }
}
