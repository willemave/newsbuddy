//
//  SharedContainer.swift
//  newsly
//
//  Created by Assistant on 11/19/25.
//

import Foundation

enum E2ETestLaunch {
    private static let arguments = ProcessInfo.processInfo.arguments
    private static let defaults = UserDefaults.standard
    private static let argumentValues = parsedArgumentValues()

    private static func parsedArgumentValues() -> [String: String] {
        var values: [String: String] = [:]
        let launchArguments = Array(arguments.dropFirst())
        var index = 0

        while index < launchArguments.count {
            let rawArgument = launchArguments[index]
            let normalizedArgument = normalizeArgumentKey(rawArgument)

            if let separatorIndex = normalizedArgument.firstIndex(of: "=") {
                let key = String(normalizedArgument[..<separatorIndex])
                let value = String(normalizedArgument[normalizedArgument.index(after: separatorIndex)...])
                values[key] = value
                index += 1
                continue
            }

            let nextIndex = index + 1
            if nextIndex < launchArguments.count {
                let nextArgument = launchArguments[nextIndex]
                if !nextArgument.hasPrefix("-") {
                    values[normalizedArgument] = nextArgument
                    index += 2
                    continue
                }
            }

            values[normalizedArgument] = "true"
            index += 1
        }

        return values
    }

    private static func normalizeArgumentKey(_ rawArgument: String) -> String {
        var normalized = rawArgument
        while normalized.hasPrefix("-") {
            normalized.removeFirst()
        }
        return normalized
    }

    private static func rawValue(for key: String) -> Any? {
        if let argumentValue = argumentValues[key] {
            return argumentValue
        }
        return defaults.object(forKey: key)
    }

    private static func string(for key: String) -> String? {
        switch rawValue(for: key) {
        case let value as String:
            let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        case let value?:
            let rendered = String(describing: value).trimmingCharacters(in: .whitespacesAndNewlines)
            return rendered.isEmpty ? nil : rendered
        default:
            return nil
        }
    }

    private static func bool(for key: String) -> Bool {
        switch rawValue(for: key) {
        case let value as Bool:
            return value
        case let value as NSNumber:
            return value.boolValue
        case let value as String:
            return ["1", "true", "yes"].contains(value.lowercased())
        default:
            if let argumentValue = argumentValues[key] {
                return ["1", "true", "yes"].contains(argumentValue.lowercased())
            }
            return arguments.contains(key) || arguments.contains("-\(key)")
        }
    }

    private static func int(for key: String) -> Int? {
        switch rawValue(for: key) {
        case let value as Int:
            return value
        case let value as NSNumber:
            return value.intValue
        case let value as String:
            return Int(value)
        default:
            return nil
        }
    }

    private static func date(for key: String) -> Date? {
        guard let value = string(for: key) else { return nil }

        let fractionalFormatter = ISO8601DateFormatter()
        fractionalFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = fractionalFormatter.date(from: value) {
            return date
        }

        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: value)
    }

    static let enabledKey = "newslyE2EEnabled"
    static let autoLoginKey = "newslyE2EAutoLogin"
    static let serverHostKey = "newslyE2EServerHost"
    static let serverPortKey = "newslyE2EServerPort"
    static let useHTTPSKey = "newslyE2EUseHTTPS"
    static let userIDKey = "newslyE2EUserId"
    static let completeOnboardingKey = "newslyE2ECompleteOnboarding"
    static let completeTutorialKey = "newslyE2ECompleteTutorial"
    static let onboardingFixtureKey = "newslyE2EOnboardingFixture"
    static let openChatSessionIdKey = "newslyE2EOpenChatSessionId"
    static let openContentIdKey = "newslyE2EOpenContentId"
    static let openContentTypeKey = "newslyE2EOpenContentType"
    static let fakeSpeechEnabledKey = "newslyE2EFakeSpeechEnabled"
    static let fakeSpeechTranscriptKey = "newslyE2EFakeSpeechTranscript"
    static let visualNowKey = "newslyE2EVisualNow"

    static var isEnabled: Bool {
        bool(for: enabledKey)
    }

    static var shouldAutoLogin: Bool {
        isEnabled && bool(for: autoLoginKey)
    }

    static var serverHost: String? {
        guard isEnabled else { return nil }
        return string(for: serverHostKey)
    }

    static var serverPort: String? {
        guard isEnabled else { return nil }
        return string(for: serverPortKey)
    }

    static var useHTTPS: Bool? {
        guard isEnabled else { return nil }
        if rawValue(for: useHTTPSKey) == nil
            && !arguments.contains(useHTTPSKey)
            && !arguments.contains("-\(useHTTPSKey)") {
            return nil
        }
        return bool(for: useHTTPSKey)
    }

    static var userID: Int? {
        guard isEnabled else { return nil }
        return int(for: userIDKey)
    }

    static var completeOnboarding: Bool {
        bool(for: completeOnboardingKey)
    }

    static var completeTutorial: Bool {
        bool(for: completeTutorialKey)
    }

    static var onboardingFixture: String? {
        guard isEnabled else { return nil }
        return string(for: onboardingFixtureKey)
    }

    static var openChatSessionId: Int? {
        guard isEnabled else { return nil }
        return int(for: openChatSessionIdKey)
    }

    static var openContentId: Int? {
        guard isEnabled else { return nil }
        return int(for: openContentIdKey)
    }

    static var openContentType: String? {
        guard isEnabled else { return nil }
        return string(for: openContentTypeKey)
    }

    static var fakeSpeechEnabled: Bool {
        isEnabled && bool(for: fakeSpeechEnabledKey)
    }

    static var fakeSpeechTranscript: String? {
        guard fakeSpeechEnabled else { return nil }
        return string(for: fakeSpeechTranscriptKey)
    }

    static var visualNow: Date? {
        guard isEnabled else { return nil }
        return date(for: visualNowKey)
    }
}

enum SharedContainer {
    private static let sharedKeychainInfoKey = "NewslySharedKeychainAccessGroup"

    /// App group identifier shared between the main app and extensions.
    /// Set this to your configured App Group (must match entitlements).
    static let appGroupId: String? = "group.com.newsly"

    /// Optional keychain access group shared between the main app and extensions.
    /// On device, prefer the shared keychain group declared in target Info.plists.
    /// On simulator, disable it to avoid entitlement-related keychain failures.
    static var keychainAccessGroup: String? {
#if targetEnvironment(simulator)
        return nil
#else
        guard let value = Bundle.main.object(forInfoDictionaryKey: sharedKeychainInfoKey) as? String else {
            return nil
        }

        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
#endif
    }

    static var userDefaults: UserDefaults {
        if E2ETestLaunch.isEnabled {
            return .standard
        }
        if let appGroupId, let defaults = UserDefaults(suiteName: appGroupId) {
            return defaults
        }
        return .standard
    }
}

enum ShareURLHandlerKind: String {
    case xShare = "x_share"
    case youtubeSingleVideo = "youtube_single_video"
    case youtubeShare = "youtube_share"
    case applePodcastShare = "apple_podcast_share"
    case podcastPlatformShare = "podcast_platform_share"
    case generic = "generic"
}

struct ShareURLHandlerMatch: Equatable {
    let kind: ShareURLHandlerKind
    let platform: String?
}

enum ShareURLRouting {
    private static let podcastHostPlatforms: [String: String] = [
        "open.spotify.com": "spotify",
        "spotify.link": "spotify",
        "spoti.fi": "spotify",
        "on.spotify.com": "spotify",
        "open.spotify.link": "spotify",
        "podcasters.spotify.com": "spotify",
        "podcasts.apple.com": "apple_podcasts",
        "music.apple.com": "apple_music",
        "overcast.fm": "overcast",
        "pca.st": "pocket_casts",
        "pocketcasts.com": "pocket_casts",
        "rss.com": "rss",
        "podcastaddict.com": "podcast_addict",
        "castbox.fm": "castbox"
    ]

    private static let applePodcastHosts: Set<String> = [
        "podcasts.apple.com",
        "music.apple.com"
    ]

    private static let youtubeHosts: Set<String> = [
        "youtube.com",
        "m.youtube.com",
        "youtu.be"
    ]

    private static let xShareHosts: Set<String> = [
        "x.com",
        "twitter.com",
        "mobile.x.com",
        "mobile.twitter.com"
    ]

    static func handler(for url: URL) -> ShareURLHandlerMatch {
        guard
            let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
            let host = normalizedHost(components.host)
        else {
            return ShareURLHandlerMatch(kind: .generic, platform: nil)
        }

        if isXStatusURL(components: components, host: host) {
            return ShareURLHandlerMatch(kind: .xShare, platform: "twitter")
        }

        if isYouTubeSingleVideo(components: components, host: host) {
            return ShareURLHandlerMatch(kind: .youtubeSingleVideo, platform: "youtube")
        }

        if youtubeHosts.contains(host) {
            return ShareURLHandlerMatch(kind: .youtubeShare, platform: "youtube")
        }

        if applePodcastHosts.contains(host) {
            return ShareURLHandlerMatch(
                kind: .applePodcastShare,
                platform: podcastHostPlatforms[host]
            )
        }

        if let platform = podcastHostPlatforms[host] {
            return ShareURLHandlerMatch(kind: .podcastPlatformShare, platform: platform)
        }

        return ShareURLHandlerMatch(kind: .generic, platform: nil)
    }

    static func preferredURL(current: URL?, candidate: URL) -> URL {
        guard let current else { return candidate }

        let candidateRank = rank(url: candidate)
        let currentRank = rank(url: current)

        if candidateRank > currentRank {
            return candidate
        }

        if candidateRank == currentRank,
           candidate.absoluteString.count > current.absoluteString.count {
            return candidate
        }

        return current
    }

    static func extractURLs(from text: String) -> [URL] {
        guard let detector = try? NSDataDetector(
            types: NSTextCheckingResult.CheckingType.link.rawValue
        ) else {
            return []
        }

        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        var urls: [URL] = []
        var seen: Set<String> = []

        detector.matches(in: text, options: [], range: range).forEach { match in
            guard let url = match.url, isWebURL(url) else { return }
            let key = url.absoluteString
            guard !seen.contains(key) else { return }
            seen.insert(key)
            urls.append(url)
        }

        return urls
    }

    static func rank(url: URL) -> Int {
        let match = handler(for: url)
        return handlerPriority(for: match.kind) + qualityScore(url)
    }

    private static func isWebURL(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased() else { return false }
        return scheme == "http" || scheme == "https"
    }

    private static func normalizedHost(_ host: String?) -> String? {
        guard var host else { return nil }
        host = host.lowercased()
        if host.hasPrefix("www.") {
            host = String(host.dropFirst(4))
        }
        return host
    }

    private static func isYouTubeSingleVideo(components: URLComponents, host: String) -> Bool {
        let lowercasedPath = components.path.lowercased()
        let trimmedPath = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))

        if host == "youtu.be" {
            return !trimmedPath.isEmpty
        }

        guard host == "youtube.com" || host == "m.youtube.com" else {
            return false
        }

        if lowercasedPath == "/watch" {
            guard let queryItems = components.queryItems else { return false }
            let videoID = queryItems.first(where: { $0.name == "v" })?.value?.trimmingCharacters(
                in: .whitespacesAndNewlines
            )
            return videoID?.isEmpty == false
        }

        let pathPrefixes = ["/shorts/", "/live/", "/embed/", "/v/"]
        return pathPrefixes.contains(where: { prefix in
            lowercasedPath.hasPrefix(prefix) && lowercasedPath.count > prefix.count
        })
    }

    private static func isXStatusURL(components: URLComponents, host: String) -> Bool {
        guard xShareHosts.contains(host) else { return false }
        let pathParts = components.path
            .split(separator: "/", omittingEmptySubsequences: true)
            .map { $0.lowercased() }
        guard !pathParts.isEmpty else { return false }

        if pathParts.count >= 2 && pathParts[0] == "i" && pathParts[1] == "status" {
            return true
        }
        if let statusIndex = pathParts.firstIndex(of: "status"), statusIndex + 1 < pathParts.count {
            return true
        }
        return false
    }

    private static func handlerPriority(for kind: ShareURLHandlerKind) -> Int {
        switch kind {
        case .xShare:
            return 380
        case .youtubeSingleVideo:
            return 400
        case .applePodcastShare:
            return 350
        case .podcastPlatformShare:
            return 300
        case .youtubeShare:
            return 250
        case .generic:
            return 0
        }
    }

    private static func qualityScore(_ url: URL) -> Int {
        var score = 0

        if let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https" {
            score += 1
        }

        if let components = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            if let queryItems = components.queryItems, !queryItems.isEmpty {
                score += 2
                score += min(queryItems.count, 3)
            }

            if let fragment = components.fragment, !fragment.isEmpty {
                score += 1
            }
        }

        return score
    }
}
