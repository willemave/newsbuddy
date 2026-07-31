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
        let viewModel = RootDependencyFactory.makeTweetSuggestionsViewModel()

        XCTAssertEqual(viewModel.selectedProvider, .openai)
    }

    private func makeSession(model: String) -> ChatSessionSummary {
        ChatSessionSummary(
            id: 1,
            contentId: nil,
            title: nil,
            sessionType: nil,
            topic: nil,
            llmProvider: "openai",
            llmModel: model,
            createdAt: Date(timeIntervalSince1970: 0),
            updatedAt: nil,
            lastMessageAt: nil,
            articleTitle: nil,
            articleUrl: nil,
            articleSummary: nil,
            articleSource: nil,
            hasPendingMessage: nil,
            isSavedToKnowledge: nil,
            hasMessages: nil,
            lastMessagePreview: nil,
            lastMessageRole: nil
        )
    }
}
