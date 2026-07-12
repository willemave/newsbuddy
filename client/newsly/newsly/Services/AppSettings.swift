//
//  AppSettings.swift
//  newsly
//
//  Created by Assistant on 7/9/25.
//

import Foundation
import Observation
import os.log

private let appSettingsLogger = Logger(
    subsystem: Bundle.main.bundleIdentifier ?? "org.willemaw.newsly",
    category: "AppSettings"
)

enum ServerConfigurationDefaults {
    static let hostKey = "serverHost"
    static let portKey = "serverPort"
    static let useHTTPSKey = "useHTTPS"
    static let defaultHost = "localhost"
    static let defaultPort = "8000"

    static func applyDebugDefaultsIfNeeded(to userDefaults: UserDefaults) {
#if DEBUG
        let persistedHost = persistedString(forKey: hostKey, in: userDefaults)
        let persistedPort = persistedString(forKey: portKey, in: userDefaults)

        guard persistedHost == nil || persistedPort == nil else {
            return
        }

        if persistedHost == nil {
            userDefaults.set(defaultHost, forKey: hostKey)
        }

        if persistedPort == nil {
            userDefaults.set(defaultPort, forKey: portKey)
        }

        if userDefaults.object(forKey: useHTTPSKey) == nil {
            userDefaults.set(false, forKey: useHTTPSKey)
        }

        appSettingsLogger.notice(
            "Seeded debug server configuration host=\(persistedHost ?? defaultHost, privacy: .public) port=\(persistedPort ?? defaultPort, privacy: .public)"
        )
#endif
    }

    static func hasPersistedServerConfiguration(in userDefaults: UserDefaults) -> Bool {
        persistedString(forKey: hostKey, in: userDefaults) != nil
            && persistedString(forKey: portKey, in: userDefaults) != nil
    }

    private static func persistedString(forKey key: String, in userDefaults: UserDefaults) -> String? {
        guard let value = userDefaults.string(forKey: key)?
            .trimmingCharacters(in: .whitespacesAndNewlines),
              !value.isEmpty else {
            return nil
        }
        return value
    }

    static func applyLaunchOverridesIfNeeded(to userDefaults: UserDefaults) {
        guard E2ETestLaunch.isEnabled else {
            return
        }

        if let host = E2ETestLaunch.serverHost {
            userDefaults.set(host, forKey: hostKey)
        }

        if let port = E2ETestLaunch.serverPort {
            userDefaults.set(port, forKey: portKey)
        }

        if let useHTTPS = E2ETestLaunch.useHTTPS {
            userDefaults.set(useHTTPS, forKey: useHTTPSKey)
        }

        if let readingExperience = E2ETestLaunch.readingExperience {
            userDefaults.set(readingExperience, forKey: "readingExperience")
        }

        appSettingsLogger.notice(
            "Applied E2E launch overrides host=\(userDefaults.string(forKey: hostKey) ?? "unset", privacy: .public) port=\(userDefaults.string(forKey: portKey) ?? "unset", privacy: .public)"
        )
    }
}

typealias ReadingExperience = APIReadingExperience

enum ReadingExperiencePolicy {
    static func presentationExperience(
        serverExperience: ReadingExperience,
        allowsClassicFallback: Bool
    ) -> ReadingExperience {
        allowsClassicFallback ? serverExperience : .briefing
    }
}

@Observable
final class AppSettings {
    static let shared = AppSettings()

    var serverHost: String {
        didSet { userDefaults.set(serverHost, forKey: ServerConfigurationDefaults.hostKey) }
    }
    var serverPort: String {
        didSet { userDefaults.set(serverPort, forKey: ServerConfigurationDefaults.portKey) }
    }
    var useHTTPS: Bool {
        didSet { userDefaults.set(useHTTPS, forKey: ServerConfigurationDefaults.useHTTPSKey) }
    }
    var appTextSizeIndex: Int {
        didSet { userDefaults.set(appTextSizeIndex, forKey: "appTextSizeIndex") }
    }
    var contentTextSizeIndex: Int {
        didSet { userDefaults.set(contentTextSizeIndex, forKey: "contentTextSizeIndex") }
    }
    var backendTranscriptionAvailable: Bool {
        didSet { userDefaults.set(backendTranscriptionAvailable, forKey: "backendTranscriptionAvailable") }
    }
    var readingExperienceRaw: String {
        didSet { userDefaults.set(readingExperienceRaw, forKey: "readingExperience") }
    }

    @ObservationIgnored
    private let userDefaults: UserDefaults

    private var hasExplicitServerConfiguration: Bool {
        ServerConfigurationDefaults.hasPersistedServerConfiguration(in: userDefaults)
    }
    private var normalizedHost: String {
#if targetEnvironment(simulator)
        if serverHost.caseInsensitiveCompare("localhost") == .orderedSame {
            return "127.0.0.1"
        }
#endif
        return serverHost
    }

    var baseURL: String {
        if !hasExplicitServerConfiguration {
            appSettingsLogger.fault("Using implicit default server configuration")
#if DEBUG
            preconditionFailure("Server host/port must be configured explicitly in debug builds")
#endif
        }
        let scheme = useHTTPS ? "https" : "http"
        return "\(scheme)://\(normalizedHost):\(serverPort)"
    }

    var readingExperience: ReadingExperience {
        ReadingExperience(rawValue: readingExperienceRaw) ?? .briefing
    }

    func setAppTextSize(_ index: Int) {
        guard appTextSizeIndex != index else { return }
        appTextSizeIndex = index
    }

    func setContentTextSize(_ index: Int) {
        guard contentTextSizeIndex != index else { return }
        contentTextSizeIndex = index
    }

    func setBackendTranscriptionAvailable(_ isAvailable: Bool) {
        guard backendTranscriptionAvailable != isAvailable else { return }
        backendTranscriptionAvailable = isAvailable
    }

    func setReadingExperience(_ experience: ReadingExperience) {
        guard readingExperience != experience else { return }
        readingExperienceRaw = experience.rawValue
    }

    private init(userDefaults: UserDefaults = SharedContainer.userDefaults) {
        self.userDefaults = userDefaults
        ServerConfigurationDefaults.applyDebugDefaultsIfNeeded(to: userDefaults)
        ServerConfigurationDefaults.applyLaunchOverridesIfNeeded(to: userDefaults)
        serverHost = userDefaults.string(forKey: ServerConfigurationDefaults.hostKey) ?? ServerConfigurationDefaults.defaultHost
        serverPort = userDefaults.string(forKey: ServerConfigurationDefaults.portKey) ?? ServerConfigurationDefaults.defaultPort
        useHTTPS = userDefaults.object(forKey: ServerConfigurationDefaults.useHTTPSKey) as? Bool ?? false
        appTextSizeIndex = userDefaults.object(forKey: "appTextSizeIndex") as? Int ?? 1
        contentTextSizeIndex = userDefaults.object(forKey: "contentTextSizeIndex") as? Int ?? 2
        backendTranscriptionAvailable = userDefaults.object(forKey: "backendTranscriptionAvailable") as? Bool ?? false
        readingExperienceRaw = userDefaults.string(forKey: "readingExperience") ?? ReadingExperience.briefing.rawValue
    }
}
