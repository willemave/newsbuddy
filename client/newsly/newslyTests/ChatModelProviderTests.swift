import XCTest
@testable import newsly

final class ChatModelProviderTests: XCTestCase {
    func testSelectableProvidersExcludeGoogleAndDeepResearch() {
        XCTAssertEqual(ChatModelProvider.selectableProviders, [.openai, .anthropic])
        XCTAssertFalse(ChatModelProvider.selectableProviders.contains(.google))
        XCTAssertFalse(ChatModelProvider.selectableProviders.contains(.deep_research))
    }

    func testTweetProvidersUseSharedSelectableProviders() {
        XCTAssertEqual(
            ChatModelProvider.tweetProviders,
            ChatModelProvider.selectableProviders
        )
    }

    func testGoogleRemainsDecodableForExistingSessions() throws {
        let provider = try JSONDecoder().decode(
            ChatModelProvider.self,
            from: Data(#""google""#.utf8)
        )

        XCTAssertEqual(provider, .google)
        XCTAssertEqual(provider.displayName, "Gemini")
    }

    @MainActor
    func testTweetSuggestionsDefaultProviderIsOpenAI() {
        let viewModel = RootDependencyFactory.makeTweetSuggestionsViewModel()

        XCTAssertEqual(viewModel.selectedProvider, .openai)
    }
}
