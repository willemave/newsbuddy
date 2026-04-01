import Foundation
import XCTest
@testable import newsly

final class AppSettingsTests: XCTestCase {
    func testApplyDebugDefaultsIfNeededSeedsMissingServerConfiguration() {
        let isolated = makeIsolatedDefaults()
        let defaults = isolated.defaults
        defer { clear(isolated.suiteName, defaults: defaults) }

        ServerConfigurationDefaults.applyDebugDefaultsIfNeeded(to: defaults)

        XCTAssertTrue(ServerConfigurationDefaults.hasPersistedServerConfiguration(in: defaults))
        XCTAssertEqual(defaults.string(forKey: ServerConfigurationDefaults.hostKey), "localhost")
        XCTAssertEqual(defaults.string(forKey: ServerConfigurationDefaults.portKey), "8000")
        XCTAssertEqual(defaults.object(forKey: ServerConfigurationDefaults.useHTTPSKey) as? Bool, false)
    }

    func testApplyDebugDefaultsIfNeededPreservesExistingHostAndBackfillsPort() {
        let isolated = makeIsolatedDefaults()
        let defaults = isolated.defaults
        defer { clear(isolated.suiteName, defaults: defaults) }
        defaults.set("192.168.1.44", forKey: ServerConfigurationDefaults.hostKey)

        ServerConfigurationDefaults.applyDebugDefaultsIfNeeded(to: defaults)

        XCTAssertTrue(ServerConfigurationDefaults.hasPersistedServerConfiguration(in: defaults))
        XCTAssertEqual(defaults.string(forKey: ServerConfigurationDefaults.hostKey), "192.168.1.44")
        XCTAssertEqual(defaults.string(forKey: ServerConfigurationDefaults.portKey), "8000")
    }

    func testHasPersistedServerConfigurationRequiresHostAndPort() {
        let isolated = makeIsolatedDefaults()
        let defaults = isolated.defaults
        defer { clear(isolated.suiteName, defaults: defaults) }
        defaults.set("192.168.1.44", forKey: ServerConfigurationDefaults.hostKey)

        XCTAssertFalse(ServerConfigurationDefaults.hasPersistedServerConfiguration(in: defaults))
    }

    private func makeIsolatedDefaults(
        file: StaticString = #filePath,
        line: UInt = #line
    ) -> (defaults: UserDefaults, suiteName: String) {
        let suiteName = "AppSettingsTests.\(UUID().uuidString)"
        guard let defaults = UserDefaults(suiteName: suiteName) else {
            XCTFail("Failed to create isolated UserDefaults", file: file, line: line)
            fatalError("Failed to create isolated UserDefaults")
        }
        defaults.removePersistentDomain(forName: suiteName)
        return (defaults, suiteName)
    }

    private func clear(_ suiteName: String, defaults: UserDefaults) {
        defaults.removePersistentDomain(forName: suiteName)
    }
}
