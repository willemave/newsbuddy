//
//  ScraperSettingsViewModelTests.swift
//  newslyTests
//

import XCTest
@testable import newsly

@MainActor
final class ScraperSettingsViewModelTests: XCTestCase {
    func testCadenceSummaryUsesHumanReadableUnits() {
        XCTAssertEqual(makeStats(intervalHours: 0.5).cadenceSummary, "Usually every 30 minutes")
        XCTAssertEqual(makeStats(intervalHours: 1).cadenceSummary, "Usually every 1 hour")
        XCTAssertEqual(makeStats(intervalHours: 48).cadenceSummary, "Usually every 2 days")
    }

    func testAddConfigReturnsTrueAndPrependsCreatedConfig() async {
        let created = makeConfig(id: 2, displayName: "Created")
        let service = StubScraperSettingsService(createResult: .success(created))
        let viewModel = ScraperSettingsViewModel(filterTypes: nil, service: service)
        viewModel.configs = [makeConfig(id: 1, displayName: "Existing")]

        let didAdd = await viewModel.addConfig(
            scraperType: "atom",
            displayName: "Created",
            feedURL: "https://example.com/feed.xml"
        )

        XCTAssertTrue(didAdd)
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertEqual(viewModel.configs.map(\.id), [2, 1])
    }

    func testAddConfigReturnsFalseAndKeepsFormOwnerStateOnFailure() async {
        let service = StubScraperSettingsService(createResult: .failure(TestError(message: "Feed unreachable")))
        let viewModel = ScraperSettingsViewModel(filterTypes: nil, service: service)
        viewModel.configs = [makeConfig(id: 1, displayName: "Existing")]

        let didAdd = await viewModel.addConfig(
            scraperType: "atom",
            displayName: "Broken",
            feedURL: "https://example.com/feed.xml"
        )

        XCTAssertFalse(didAdd)
        XCTAssertEqual(
            viewModel.errorMessage,
            "Newsly couldn't add this source. Check the URL and try again."
        )
        XCTAssertEqual(viewModel.configs.map(\.id), [1])
    }

    func testDeletedConfigIsNotRestoredByAnOlderInFlightLoad() async {
        let existing = makeConfig(id: 1, displayName: "Existing")
        let service = StubScraperSettingsService(
            createResult: .failure(TestError(message: "Unexpected create")),
            listedConfigs: [existing],
            deleteResult: .success(())
        )
        let viewModel = ScraperSettingsViewModel(filterTypes: nil, service: service)
        viewModel.configs = [existing]

        service.pauseNextListResponse()
        let loadTask = Task { await viewModel.loadConfigs() }
        await waitUntil { service.listResponsePaused }

        await viewModel.deleteConfig(existing)
        service.resumeListResponse()
        await loadTask.value

        XCTAssertTrue(viewModel.configs.isEmpty)
    }

    private func waitUntil(_ condition: () -> Bool) async {
        for _ in 0..<200 {
            if condition() { return }
            try? await Task.sleep(for: .milliseconds(1))
        }
        XCTFail("Condition was not satisfied before timeout")
    }

    private func makeConfig(id: Int, displayName: String) -> ScraperConfig {
        ScraperConfig(
            id: id,
            scraperType: "atom",
            displayName: displayName,
            config: [:],
            feedUrl: "https://example.com/feed-\(id).xml",
            isActive: true,
            createdAt: Date(timeIntervalSince1970: TimeInterval(id))
        )
    }

    private func makeStats(intervalHours: Double) -> ScraperConfigStats {
        ScraperConfigStats(
            totalCount: 0,
            completedCount: 0,
            unreadCount: 0,
            processingCount: 0,
            averageIntervalHours: intervalHours
        )
    }
}

private final class StubScraperSettingsService: ScraperSettingsServicing {
    let createResult: Result<ScraperConfig, Error>
    private let listedConfigs: [ScraperConfig]
    private let deleteResult: Result<Void, Error>
    private let listStateLock = NSLock()
    private var shouldPauseNextList = false
    private var shouldKeepListPaused = false

    var listResponsePaused: Bool {
        listStateLock.withLock { shouldKeepListPaused }
    }

    init(
        createResult: Result<ScraperConfig, Error>,
        listedConfigs: [ScraperConfig] = [],
        deleteResult: Result<Void, Error> = .failure(TestError(message: "Unexpected delete"))
    ) {
        self.createResult = createResult
        self.listedConfigs = listedConfigs
        self.deleteResult = deleteResult
    }

    func listConfigs(types _: [String]?, includeStats _: Bool) async throws -> [ScraperConfig] {
        let shouldPause = listStateLock.withLock {
            let result = shouldPauseNextList
            shouldPauseNextList = false
            shouldKeepListPaused = result
            return result
        }
        while shouldPause, listStateLock.withLock({ shouldKeepListPaused }) {
            try await Task.sleep(for: .milliseconds(1))
        }
        return listedConfigs
    }

    func createConfig(
        scraperType _: String,
        displayName _: String?,
        feedURL _: String,
        limit _: Int?,
        isActive _: Bool
    ) async throws -> ScraperConfig {
        try createResult.get()
    }

    func updateConfig(
        configId _: Int,
        displayName _: String?,
        feedURL _: String?,
        limit _: Int?,
        isActive _: Bool?
    ) async throws -> ScraperConfig {
        throw TestError(message: "Unexpected update")
    }

    func deleteConfig(configId _: Int) async throws {
        try deleteResult.get()
    }

    func pauseNextListResponse() {
        listStateLock.withLock { shouldPauseNextList = true }
    }

    func resumeListResponse() {
        listStateLock.withLock { shouldKeepListPaused = false }
    }
}

private struct TestError: LocalizedError {
    let message: String

    var errorDescription: String? {
        message
    }
}
