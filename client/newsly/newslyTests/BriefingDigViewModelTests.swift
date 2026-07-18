import XCTest
@testable import newsly

@MainActor
final class BriefingDigViewModelTests: XCTestCase {
    func testCacheIncludesPassageContext() async {
        let service = MockBriefingService()
        service.digSummaries = ["First summary", "Second summary"]
        let viewModel = BriefingDigViewModel(service: service)

        viewModel.dig(fragment: "same fragment", passageContext: "First passage")
        await waitForBriefingCondition { viewModel.stateKey == "loaded" }

        viewModel.dig(fragment: "same fragment", passageContext: "Second passage")
        await waitForBriefingCondition {
            guard service.digSummarizePassageContexts.count == 2,
                  case .loaded(_, let summary) = viewModel.state
            else { return false }
            return summary == "Second summary"
        }

        XCTAssertEqual(service.digSearchFragments, ["same fragment", "same fragment"])
        XCTAssertEqual(
            service.digSummarizePassageContexts,
            ["First passage", "Second passage"]
        )

        viewModel.dig(fragment: "same fragment", passageContext: "Second passage")
        XCTAssertEqual(
            service.digSearchFragments.count,
            2,
            "An identical request should use cache"
        )
        guard case .loaded(_, let summary) = viewModel.state else {
            return XCTFail("Expected the cached loaded state")
        }
        XCTAssertEqual(summary, "Second summary")
    }

    func testRetryRepeatsTheFailedRequest() async {
        let service = MockBriefingService()
        service.digSearchErrors = [NSError(domain: "BriefingDigTests", code: 1), nil]
        let viewModel = BriefingDigViewModel(service: service)

        viewModel.dig(fragment: "retry this", passageContext: "Passage context")
        await waitForBriefingCondition { viewModel.stateKey == "error" }

        viewModel.retry()
        await waitForBriefingCondition { viewModel.stateKey == "loaded" }

        XCTAssertEqual(service.digSearchFragments, ["retry this", "retry this"])
        XCTAssertEqual(service.digSummarizePassageContexts, ["Passage context"])
    }

    func testDigRejectsAFragmentLongerThanTheAPIContract() {
        let service = MockBriefingService()
        let viewModel = BriefingDigViewModel(service: service)

        viewModel.dig(
            fragment: String(repeating: "a", count: 2_001),
            passageContext: "Passage context"
        )

        XCTAssertTrue(viewModel.isIdle)
        XCTAssertTrue(service.digSearchFragments.isEmpty)
    }
}

final class BriefingDigSelectionPolicyTests: XCTestCase {
    func testNormalizeTrimsAValidSelection() {
        XCTAssertEqual(
            BriefingDigSelectionPolicy.normalize("  useful context\n"),
            "useful context"
        )
    }

    func testNormalizeRejectsSelectionsOutsideTheAPIContract() {
        XCTAssertNil(BriefingDigSelectionPolicy.normalize("no"))
        XCTAssertNil(
            BriefingDigSelectionPolicy.normalize(String(repeating: "a", count: 2_001))
        )
    }

    func testNormalizeCountsUnicodeScalarsLikeTheAPI() {
        XCTAssertNotNil(BriefingDigSelectionPolicy.normalize("👨‍👩‍👧‍👦"))
        XCTAssertNil(
            BriefingDigSelectionPolicy.normalize(String(repeating: "👍🏽", count: 1_001))
        )
    }

    func testPassageContextKeepsALateUnicodeSelectionInsideTheAPIContract() {
        let context = String(repeating: "👍🏽", count: 1_100)
            + " selected fragment near the end"

        let normalized = BriefingDigSelectionPolicy.passageContext(
            context,
            around: "selected fragment"
        )

        XCTAssertEqual(
            normalized.unicodeScalars.count,
            BriefingDigSelectionPolicy.maximumLength
        )
        XCTAssertTrue(normalized.contains("selected fragment near the end"))
    }
}
