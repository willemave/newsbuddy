//
//  UserMessageBubble.swift
//  newsly
//

import SwiftUI

struct UserMessageBubble: View {
    let message: ChatMessage
    private let leadingClearance: CGFloat = 72

    var body: some View {
        HStack(alignment: .top, spacing: 6) {
            Spacer(minLength: leadingClearance)

            VStack(alignment: .trailing, spacing: 4) {
                Text(message.content)
                    .font(.appCallout)
                    .foregroundStyle(.white)
                    .textSelection(.enabled)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(Color.chatUserBubble.opacity(0.92))
                    .clipShape(bubbleShape)

                if !message.formattedTime.isEmpty {
                    Text(message.formattedTime)
                        .font(.appCaption2)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .padding(.horizontal, 4)
                }
            }
            .contextMenu {
                Button {
                    UIPasteboard.general.string = message.content
                } label: {
                    Label("Copy", systemImage: "doc.on.doc")
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .trailing)
    }

    private var bubbleShape: UnevenRoundedRectangle {
        UnevenRoundedRectangle(
            topLeadingRadius: 14,
            bottomLeadingRadius: 14,
            bottomTrailingRadius: 14,
            topTrailingRadius: 14
        )
    }
}

#if DEBUG
#Preview("User Message Bubble") {
    UserMessageBubble(message: ChatPreviewFixtures.userMessage)
        .padding()
        .background(Color.surfacePrimary)
}
#endif
