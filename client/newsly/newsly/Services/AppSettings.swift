//
//  AppSettings.swift
//  newsly
//
//  Created by Assistant on 7/9/25.
//

import Combine
import Foundation
import SwiftUI
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

        appSettingsLogger.notice(
            "Applied E2E launch overrides host=\(userDefaults.string(forKey: hostKey) ?? "unset", privacy: .public) port=\(userDefaults.string(forKey: portKey) ?? "unset", privacy: .public)"
        )
    }
}

class AppSettings: ObservableObject {
    static let shared = AppSettings()
    
    @AppStorage("serverHost", store: SharedContainer.userDefaults) var serverHost: String = "localhost"
    @AppStorage("serverPort", store: SharedContainer.userDefaults) var serverPort: String = "8000"
    @AppStorage("useHTTPS", store: SharedContainer.userDefaults) var useHTTPS: Bool = false
    @AppStorage("appTextSizeIndex", store: SharedContainer.userDefaults) var appTextSizeIndex: Int = 1
    @AppStorage("contentTextSizeIndex", store: SharedContainer.userDefaults) var contentTextSizeIndex: Int = 2
    @AppStorage("useLongFormCardStack", store: SharedContainer.userDefaults) var useLongFormCardStack: Bool = true
    @AppStorage("backendTranscriptionAvailable", store: SharedContainer.userDefaults) var backendTranscriptionAvailable: Bool = false
    private var hasExplicitServerConfiguration: Bool {
        ServerConfigurationDefaults.hasPersistedServerConfiguration(in: SharedContainer.userDefaults)
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

    func setBackendTranscriptionAvailable(_ isAvailable: Bool) {
        backendTranscriptionAvailable = isAvailable
    }
    
    private init() {
        ServerConfigurationDefaults.applyDebugDefaultsIfNeeded(to: SharedContainer.userDefaults)
        ServerConfigurationDefaults.applyLaunchOverridesIfNeeded(to: SharedContainer.userDefaults)
    }
}
