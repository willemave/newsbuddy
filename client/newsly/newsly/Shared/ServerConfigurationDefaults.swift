//
//  ServerConfigurationDefaults.swift
//  newsly
//

import Foundation
import os.log

private let serverConfigurationLogger = Logger(
    subsystem: Bundle.main.bundleIdentifier ?? "org.willemaw.newsly",
    category: "ServerConfiguration"
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

        serverConfigurationLogger.notice(
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

    static func baseURL(in userDefaults: UserDefaults) -> URL? {
        let configuration = resolvedConfiguration(in: userDefaults)
        var host = configuration.host
#if targetEnvironment(simulator)
        if host.caseInsensitiveCompare("localhost") == .orderedSame {
            host = "127.0.0.1"
        }
#endif
        let scheme = configuration.useHTTPS ? "https" : "http"
        return URL(string: "\(scheme)://\(host):\(configuration.port)")
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
