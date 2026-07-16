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

    static func resolvedConfiguration(
        in userDefaults: UserDefaults,
        launchHost: String? = nil,
        launchPort: String? = nil,
        launchUseHTTPS: Bool? = nil
    ) -> (host: String, port: String, useHTTPS: Bool) {
        (
            host: launchHost ?? persistedString(forKey: hostKey, in: userDefaults) ?? defaultHost,
            port: launchPort ?? persistedString(forKey: portKey, in: userDefaults) ?? defaultPort,
            useHTTPS: launchUseHTTPS
                ?? (userDefaults.object(forKey: useHTTPSKey) as? Bool)
                ?? false
        )
    }

    private static func persistedString(forKey key: String, in userDefaults: UserDefaults) -> String? {
        guard let value = userDefaults.string(forKey: key)?
            .trimmingCharacters(in: .whitespacesAndNewlines),
              !value.isEmpty else {
            return nil
        }
        return value
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
        let serverConfiguration = ServerConfigurationDefaults.resolvedConfiguration(
            in: userDefaults,
            launchHost: E2ETestLaunch.serverHost,
            launchPort: E2ETestLaunch.serverPort,
            launchUseHTTPS: E2ETestLaunch.useHTTPS
        )
        serverHost = serverConfiguration.host
        serverPort = serverConfiguration.port
        useHTTPS = serverConfiguration.useHTTPS
        appTextSizeIndex = userDefaults.object(forKey: "appTextSizeIndex") as? Int ?? 1
        contentTextSizeIndex = userDefaults.object(forKey: "contentTextSizeIndex") as? Int ?? 2
        backendTranscriptionAvailable = userDefaults.object(forKey: "backendTranscriptionAvailable") as? Bool ?? false
        readingExperienceRaw = E2ETestLaunch.readingExperience
            ?? userDefaults.string(forKey: "readingExperience")
            ?? ReadingExperience.briefing.rawValue

        if E2ETestLaunch.isEnabled {
            appSettingsLogger.notice(
                "Applied ephemeral E2E server configuration host=\(self.serverHost, privacy: .public) port=\(self.serverPort, privacy: .public)"
            )
        }
    }
}
