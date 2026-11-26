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

/// How News reads on the open display of a foldable. Phones always use one
/// column.
enum NewsColumnPreference: String, CaseIterable, Identifiable {
    case single
    case double

    var id: String { rawValue }

    static let storageKey = "openDisplayNewsColumns"

    static func stored(in defaults: UserDefaults) -> NewsColumnPreference {
        defaults.string(forKey: storageKey).flatMap(NewsColumnPreference.init(rawValue:)) ?? .double
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
    /// Per-device reader preference for News on the open display.
    var openDisplayNewsColumns: NewsColumnPreference {
        didSet { userDefaults.set(openDisplayNewsColumns.rawValue, forKey: NewsColumnPreference.storageKey) }
    }
    /// Remembered once this device reports a hinge, so open-display settings
    /// stay visible while it is closed.
    var deviceHasHinge: Bool {
        didSet { userDefaults.set(deviceHasHinge, forKey: "deviceHasHinge") }
    }

    @ObservationIgnored
    private let userDefaults: UserDefaults

    private var hasExplicitServerConfiguration: Bool {
        ServerConfigurationDefaults.hasPersistedServerConfiguration(in: userDefaults)
    }
    var baseURL: String {
        if !hasExplicitServerConfiguration {
            appSettingsLogger.fault("Using implicit default server configuration")
#if DEBUG
            preconditionFailure("Server host/port must be configured explicitly in debug builds")
#endif
        }
        return ServerConfigurationDefaults.baseURLString(
            host: serverHost,
            port: serverPort,
            useHTTPS: useHTTPS
        )
    }

    func setAppTextSize(_ index: Int) {
        guard appTextSizeIndex != index else { return }
        appTextSizeIndex = index
    }

    func setContentTextSize(_ index: Int) {
        guard contentTextSizeIndex != index else { return }
        contentTextSizeIndex = index
    }

    func setOpenDisplayNewsColumns(_ preference: NewsColumnPreference) {
        guard openDisplayNewsColumns != preference else { return }
        openDisplayNewsColumns = preference
    }

    func noteDeviceHasHinge() {
        guard !deviceHasHinge else { return }
        deviceHasHinge = true
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
        openDisplayNewsColumns = E2ETestLaunch.newsColumns.flatMap(NewsColumnPreference.init(rawValue:))
            ?? NewsColumnPreference.stored(in: userDefaults)
        deviceHasHinge = userDefaults.bool(forKey: "deviceHasHinge")
        if E2ETestLaunch.isEnabled {
            appSettingsLogger.notice(
                "Applied ephemeral E2E server configuration host=\(self.serverHost, privacy: .public) port=\(self.serverPort, privacy: .public)"
            )
        }
    }
}
