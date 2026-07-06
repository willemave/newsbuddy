//
//  ScraperSettingsViewModelTests.swift
//  newslyTests
//

import XCTest
@testable import newsly

@MainActor
final class ScraperSettingsViewModelTests: XCTestCase {
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
        XCTAssertEqual(viewModel.errorMessage, "Feed unreachable")
        XCTAssertEqual(viewModel.configs.map(\.id), [1])
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
}

private final class StubScraperSettingsService: ScraperSettingsServicing {
    let createResult: Result<ScraperConfig, Error>

    init(createResult: Result<ScraperConfig, Error>) {
        self.createResult = createResult
    }

    func listConfigs(types _: [String]?, includeStats _: Bool) async throws -> [ScraperConfig] {
        []
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
        throw TestError(message: "Unexpected delete")
    }
}

private struct TestError: LocalizedError {
    let message: String

    var errorDescription: String? {
        message
    }
}
