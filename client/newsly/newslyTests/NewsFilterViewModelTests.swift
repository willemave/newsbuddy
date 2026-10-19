import XCTest
@testable import newsly

@MainActor
final class NewsFilterViewModelTests: XCTestCase {
    func testStatusLabelsDistinguishExistingCoverageAndPreviewUnavailability() {
        XCTAssertEqual(newsFilterStatusText("published"), "Already in your Briefing")
        XCTAssertEqual(newsFilterStatusText("read"), "Already read")
        XCTAssertEqual(newsFilterStatusText("unavailable"), "Could not evaluate — kept visible")
    }

    func testApplyPreservesExactTextAndUsesLoadedRevision() async {
        let service = FakeNewsFilterService()
        let model = NewsFilterViewModel(service: service)
        await model.load()
        model.text = "  Space launches; never sports.\nKeep negation.  "
        model.enabled = true
        await model.apply()
        XCTAssertEqual(service.savedText, "  Space launches; never sports.\nKeep negation.  ")
        XCTAssertEqual(service.savedRevision, 4)
        XCTAssertTrue(service.savedEnabled == true)
        XCTAssertEqual(model.preference?.revision, 5)
    }

    func testConflictRetainsDraftUntilExplicitReload() async {
        let service = FakeNewsFilterService()
        let model = NewsFilterViewModel(service: service)
        await model.load()
        model.text = "my draft"
        service.saveError = ClientFailure.http(statusCode: 409, detail: "revision conflict")
        await model.apply()
        XCTAssertEqual(model.text, "my draft")
        XCTAssertTrue(model.needsReload)
        XCTAssertFalse(model.canApply)
        await model.load()
        XCTAssertEqual(model.text, "original")
        XCTAssertFalse(model.needsReload)
    }

    func testApplyRequiresChangeFromSavedFilter() async {
        let service = FakeNewsFilterService()
        let model = NewsFilterViewModel(service: service)
        await model.load()
        XCTAssertFalse(model.canApply)
        model.enabled = true
        XCTAssertTrue(model.canApply)
        model.enabled = false
        XCTAssertFalse(model.canApply)
        model.text = "original!"
        XCTAssertTrue(model.canApply)
    }

    func testBlankEnabledAndOverLimitCannotSave() async {
        let service = FakeNewsFilterService()
        let model = NewsFilterViewModel(service: service)
        model.text = "draft before loading"
        XCTAssertFalse(model.canPreview)
        await model.load()
        XCTAssertTrue(model.canPreview)
        model.text = " \n"
        model.enabled = true
        XCTAssertFalse(model.canApply)
        XCTAssertFalse(model.canPreview)
        await model.apply()
        XCTAssertNil(service.savedText)
        model.enabled = false
        XCTAssertTrue(model.canApply)
        model.text = String(repeating: "a", count: 8_001)
        XCTAssertFalse(model.canApply)
        XCTAssertFalse(model.canPreview)
    }

    func testMultibyteActivationAndPreviewRespectUTF8Limit() async {
        let service = FakeNewsFilterService()
        let model = NewsFilterViewModel(service: service)
        await model.load()
        model.text = String(repeating: "界", count: 3_000)
        model.enabled = true
        XCTAssertFalse(model.canApply)
        XCTAssertFalse(model.canPreview)
        await model.preview()
        XCTAssertNil(service.previewText)
        model.enabled = false
        XCTAssertTrue(model.canApply)
        await model.apply()
        XCTAssertEqual(service.savedEnabled, false)
    }

    func testPreviewUsesDraftWithoutSaving() async {
        let service = FakeNewsFilterService()
        let model = NewsFilterViewModel(service: service)
        await model.load()
        model.text = "unsaved draft"
        await model.preview()
        XCTAssertEqual(service.previewText, "unsaved draft")
        XCTAssertNil(service.savedText)
        XCTAssertEqual(model.preference?.text, "original")
    }
}

private final class FakeNewsFilterService: NewsFilterServicing {
    var savedText: String?
    var savedRevision: Int?
    var savedEnabled: Bool?
    var previewText: String?
    var saveError: Error?

    func load() async throws -> APINewsFilterPreference {
        APINewsFilterPreference(revision: 4, text: "original", enabled: false)
    }
    func save(revision: Int, text: String, enabled: Bool) async throws -> APINewsFilterPreference {
        if let saveError { throw saveError }
        savedText = text
        savedRevision = revision
        savedEnabled = enabled
        return APINewsFilterPreference(revision: revision + 1, text: text, enabled: enabled)
    }
    func preview(text: String) async throws -> APINewsFilterPreviewResponse {
        previewText = text
        return APINewsFilterPreviewResponse(items: [])
    }
    func news(filtered: Bool, cursor: String?) async throws -> APINewsFilterNewsResponse {
        APINewsFilterNewsResponse(items: [], nextCursor: nil)
    }
}
