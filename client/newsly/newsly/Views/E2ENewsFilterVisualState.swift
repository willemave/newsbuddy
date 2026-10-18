#if DEBUG
import SwiftUI

struct E2ENewsFilterVisualState: View {
    private let service = E2ENewsFilterService()
    var body: some View {
        NavigationStack { NewsFilterView(service: service) }
    }
}

private final class E2ENewsFilterService: NewsFilterServicing {
    private var preference = APINewsFilterPreference(
        revision: 1, text: "Include space exploration and climate research. Exclude celebrity news and sports.", enabled: true)
    func load() async throws -> APINewsFilterPreference { preference }
    func save(revision: Int, text: String, enabled: Bool) async throws -> APINewsFilterPreference {
        preference = APINewsFilterPreference(revision: revision + 1, text: text, enabled: enabled)
        return preference
    }
    func preview(text: String) async throws -> APINewsFilterPreviewResponse {
        APINewsFilterPreviewResponse(items: [])
    }
    func news(filtered: Bool, cursor: String?) async throws -> APINewsFilterNewsResponse {
        APINewsFilterNewsResponse(items: [], nextCursor: nil)
    }
}
#endif
