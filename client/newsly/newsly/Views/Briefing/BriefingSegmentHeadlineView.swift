import SwiftUI

/// Article and podcast segments cover one full work, so they open with its
/// linked title like a traditional story headline. News roundups do not.
struct BriefingSegmentHeadline: Equatable {
    let sourceKey: String
    let title: String
    let publisher: String?
    let meta: String

    init?(segment: APIBriefingSegment, sourcesByKey: [String: APIBriefingSource]) {
        guard segment.sourceKeys.count == 1,
              let source = sourcesByKey[segment.sourceKeys[0]],
              source.kind == "content"
        else { return nil }
        let title = source.title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return nil }

        switch source.contentType {
        case .article:
            meta = Self.meta("Article", source.readingMinutes.map { "\($0) min read" })
        case .podcast:
            meta = Self.meta("Podcast", source.durationSeconds.flatMap(Self.durationLabel))
        default:
            return nil
        }
        self.sourceKey = source.sourceKey
        self.title = title
        self.publisher = source.publisher.flatMap(Self.publisherName)
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

    static func durationLabel(seconds: Int) -> String? {
        guard seconds > 0 else { return nil }
        let minutes = max(Int((Double(seconds) / 60).rounded()), 1)
        guard minutes >= 60 else { return "\(minutes) min" }
        let remainder = minutes % 60
        return remainder == 0 ? "\(minutes / 60) hr" : "\(minutes / 60) hr \(remainder) min"
    }

    private static func meta(_ kind: String, _ detail: String?) -> String {
        [kind, detail].compactMap { $0 }.joined(separator: " · ")
    }
}

struct BriefingSegmentHeadlineView: View {
    let headline: BriefingSegmentHeadline
    let segmentID: Int
    let onOpenSource: (String) -> Void

    var body: some View {
        Button {
            onOpenSource(headline.sourceKey)
        } label: {
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    if let publisher = headline.publisher {
                        Text(publisher.uppercased())
                            .font(.editorialMeta)
                            .tracking(1.6)
                            .foregroundStyle(Color.brandPrimary)
                            .lineLimit(1)
                        Spacer(minLength: 0)
                    }

                    Text(headline.meta)
                        .font(.appFootnote)
                        .foregroundStyle(Color.onSurfaceTertiary)
                        .fixedSize()
                }
                .padding(.bottom, 7)

                Text(headline.title)
                    .font(.appSerif(size: 22, relativeTo: .title2, weight: .medium))
                    .foregroundStyle(Color.onSurface)
                    .lineSpacing(1)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(BriefingHeadlineButtonStyle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            [headline.title, headline.publisher, headline.meta]
                .compactMap { $0 }
                .joined(separator: ", ")
        )
        .accessibilityHint("Opens the full source")
        .accessibilityAddTraits([.isHeader, .isLink])
        .accessibilityIdentifier("briefing.segment_headline.\(segmentID)")
    }
}

private struct BriefingHeadlineButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.6 : 1)
            .animation(.easeOut(duration: 0.15), value: configuration.isPressed)
    }
}
