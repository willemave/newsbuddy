import XCTest
@testable import newsly

final class ChatModelProviderTests: XCTestCase {
    func testSelectableProvidersIncludeOnlyOpenAIAndAnthropic() {
        XCTAssertEqual(ChatModelProvider.selectableProviders, [.openai, .anthropic])
        XCTAssertFalse(ChatModelProvider.selectableProviders.contains(.deep_research))
    }

    func testTweetProvidersUseSharedSelectableProviders() {
        XCTAssertEqual(
            ChatModelProvider.tweetProviders,
            ChatModelProvider.selectableProviders
        )
    }

    func testGoogleIsNotDecodable() {
        XCTAssertThrowsError(
            try JSONDecoder().decode(
                ChatModelProvider.self,
                from: Data(#""google""#.utf8)
            )
        )
    }

    func testOpenAISessionLabelsPreserveLegacyModels() {
        XCTAssertEqual(makeSession(model: "openai:gpt-5.6-terra").providerDisplayName, "GPT-5.6 Terra")
        XCTAssertEqual(makeSession(model: "openai:gpt-5.5").providerDisplayName, "GPT-5.5")
    }

    @MainActor
    func testTweetSuggestionsDefaultProviderIsOpenAI() {
        let viewModel = TweetSuggestionsViewModel(
            contentService: ContentService(client: makeTestAPIClient()),
            twitterService: TwitterShareService.shared,
            transcriptionService: TestUnavailableSpeechTranscriber(),
            refreshTranscriptionAvailability: { false }
        )

        XCTAssertEqual(viewModel.selectedProvider, .openai)
    }

    private func makeSession(model: String) -> ChatSessionSummary {
        .fixture(
            title: nil,
            sessionType: nil,
            llmModel: model
        )
    }
}
