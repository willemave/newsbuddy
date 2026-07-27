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

typealias ReadingExperience = APIReadingExperience

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
        if E2ETestLaunch.isEnabled {
            appSettingsLogger.notice(
                "Applied ephemeral E2E server configuration host=\(self.serverHost, privacy: .public) port=\(self.serverPort, privacy: .public)"
            )
        }
    }
}
