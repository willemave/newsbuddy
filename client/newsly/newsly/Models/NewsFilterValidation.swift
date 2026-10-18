import Foundation

enum NewsFilterValidation {
    static func message(text: String, enabled: Bool) -> String? {
        if text.unicodeScalars.count > 8_000 { return "Use 8,000 characters or fewer." }
        if enabled && text.utf8.count > 8_192 { return "Shorten this filter before enabling it." }
        if enabled && text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return "Write a filter before enabling it."
        }
        return nil
    }
}
