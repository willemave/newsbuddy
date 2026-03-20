import SwiftUI

struct QuickMicContextPayload: Equatable {
    let tab: RootTab
    let priority: Int
    let allowsQuickMic: Bool
    let context: AssistantScreenContext
}

struct QuickMicContextPreferenceKey: PreferenceKey {
    static var defaultValue: [QuickMicContextPayload] = []

    static func reduce(value: inout [QuickMicContextPayload], nextValue: () -> [QuickMicContextPayload]) {
        value.append(contentsOf: nextValue())
    }
}

private struct QuickMicContextModifier: ViewModifier {
    let payload: QuickMicContextPayload

    func body(content: Content) -> some View {
        content.preference(key: QuickMicContextPreferenceKey.self, value: [payload])
    }
}

extension View {
    func quickMicContext(
        tab: RootTab,
        priority: Int = 0,
        allowsQuickMic: Bool = true,
        context: AssistantScreenContext
    ) -> some View {
        modifier(
            QuickMicContextModifier(
                payload: QuickMicContextPayload(
                    tab: tab,
                    priority: priority,
                    allowsQuickMic: allowsQuickMic,
                    context: context
                )
            )
        )
    }
}

struct QuickMicHost<Content: View>: View {
    let tab: RootTab
    @ObservedObject var viewModel: QuickMicViewModel
    let fallbackContext: AssistantScreenContext
    let fallbackAllowsQuickMic: Bool
    let onOpenChatSession: (Int) -> Void
    @ViewBuilder let content: () -> Content

    var body: some View {
        content()
    }
}
