import Foundation

/// Resolves image paths returned by the API into loadable URLs.
///
/// The backend emits image URLs as root-relative paths (e.g. `/static/images/content/1.png`)
/// so they stay host-agnostic across environments. The client prepends the configured server
/// base URL, leaving already-absolute `http(s)` URLs untouched.
enum ServerImageURL {
    static func resolve(_ urlString: String?) -> URL? {
        guard let urlString, !urlString.isEmpty else { return nil }
        if urlString.hasPrefix("http://") || urlString.hasPrefix("https://") {
            return URL(string: urlString)
        }
        // Use string concatenation instead of appendingPathComponent to preserve path structure.
        let baseURL = AppSettings.shared.baseURL
        let fullURL = urlString.hasPrefix("/") ? baseURL + urlString : baseURL + "/" + urlString
        return URL(string: fullURL)
    }
}
