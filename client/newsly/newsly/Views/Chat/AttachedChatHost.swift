//
//  AttachedChatHost.swift
//  newsly
//

import SwiftUI

private struct AttachedChatHostKey: EnvironmentKey {
    static let defaultValue: AttachedChatHost = .root
}

extension EnvironmentValues {
    /// The host that chats started from this subtree attach to.
    var attachedChatHost: AttachedChatHost {
        get { self[AttachedChatHostKey.self] }
        set { self[AttachedChatHostKey.self] = newValue }
    }
}

extension View {
    /// Declares this view as `host` for attached chats and presents the chat
    /// as a sheet over it. When `presentsAsSheet` is false the host shows the
    /// chat itself (the open-display root gives it a page of its own), and
    /// the sheet steps aside without ending the chat.
    func attachedChatHost(
        _ host: AttachedChatHost,
        controller: AttachedChatController,
        presentsAsSheet: Bool = true,
        contentTextSize: DynamicTypeSize? = nil
    ) -> some View {
        modifier(
            AttachedChatHostModifier(
                host: host,
                controller: controller,
                presentsAsSheet: presentsAsSheet,
                contentTextSize: contentTextSize
            )
        )
    }
}

private struct AttachedChatHostModifier: ViewModifier {
    let host: AttachedChatHost
    let controller: AttachedChatController
    let presentsAsSheet: Bool
    let contentTextSize: DynamicTypeSize?

    func body(content: Content) -> some View {
        content
            .environment(\.attachedChatHost, host)
            .sheet(item: sheetBinding) { presentation in
                AttachedChatPane(presentation: presentation, controller: controller)
                    .optionalDynamicTypeSize(contentTextSize)
                    .presentationDetents([.medium, .large])
                    .presentationContentInteraction(.scrolls)
                    .presentationDragIndicator(.visible)
            }
    }

    /// Only a user dismissal ends the chat. When the binding goes nil because
    /// the chat moved hosts or this host stopped presenting sheets, SwiftUI
    /// re-reads the getter from the new body, whose guard fails, so the chat
    /// survives.
    private var sheetBinding: Binding<AttachedChatController.Presentation?> {
        Binding(
            get: {
                guard presentsAsSheet, let presentation = controller.presentation,
                      presentation.host == host else { return nil }
                return presentation
            },
            set: { newValue in
                guard newValue == nil, presentsAsSheet,
                      controller.presentation?.host == host else { return }
                controller.close()
            }
        )
    }
}

/// The chat view for an attached presentation, in any host: a sheet or an
/// open-display page. The persistent bottom chrome inset never applies here because
/// an attached chat always sits above the floating root bar.
struct AttachedChatPane: View {
    let presentation: AttachedChatController.Presentation
    let controller: AttachedChatController

    var body: some View {
        ChatSessionView(attached: presentation, controller: controller)
            .environment(\.persistentBottomChromeInset, 0)
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("chat.attached")
    }
}

private extension View {
    @ViewBuilder
    func optionalDynamicTypeSize(_ size: DynamicTypeSize?) -> some View {
        if let size {
            dynamicTypeSize(size)
        } else {
            self
        }
    }
}
