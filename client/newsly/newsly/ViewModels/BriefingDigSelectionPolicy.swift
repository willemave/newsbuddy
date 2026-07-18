import Foundation

enum BriefingDigSelectionPolicy {
    static let maximumLength = 2_000
    private static let fragmentLengthRange = 3...maximumLength

    static func normalize(_ rawSelection: String) -> String? {
        let normalized = rawSelection.trimmingCharacters(in: .whitespacesAndNewlines)
        guard fragmentLengthRange.contains(normalized.unicodeScalars.count) else { return nil }
        return normalized
    }

    static func passageContext(_ rawContext: String, around fragment: String) -> String {
        let normalized = rawContext.trimmingCharacters(in: .whitespacesAndNewlines)
        let scalars = normalized.unicodeScalars
        let scalarCount = scalars.count
        guard scalarCount > maximumLength else { return normalized }
        guard let fragmentRange = normalized.range(of: fragment, options: .caseInsensitive) else {
            return String(scalars.prefix(maximumLength))
        }

        let fragmentOffset = normalized[..<fragmentRange.lowerBound].unicodeScalars.count
        let fragmentLength = normalized[fragmentRange].unicodeScalars.count
        let preferredLeadingContext = (maximumLength - fragmentLength) / 2
        let startOffset = min(
            max(0, fragmentOffset - preferredLeadingContext),
            scalarCount - maximumLength
        )
        let startIndex = scalars.index(scalars.startIndex, offsetBy: startOffset)
        let endIndex = scalars.index(startIndex, offsetBy: maximumLength)
        return String(scalars[startIndex..<endIndex])
    }
}
