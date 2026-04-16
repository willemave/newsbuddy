//
//  UserMessageBubble.swift
//  newsly
//

import SwiftUI

struct UserMessageBubble: View {
    let message: ChatMessage

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Spacer(minLength: 40)

            VStack(alignment: .trailing, spacing: 4) {
                Text(message.content)
                    .font(.callout)
                    .foregroundStyle(.white)
                    .textSelection(.enabled)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(Color.chatUserBubble)
                    .clipShape(bubbleShape)

                if !message.formattedTime.isEmpty {
                    Text(message.formattedTime)
                        .font(.caption2)
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
    }

    private var bubbleShape: UnevenRoundedRectangle {
        UnevenRoundedRectangle(
            topLeadingRadius: 16,
            bottomLeadingRadius: 16,
            bottomTrailingRadius: 16,
            topTrailingRadius: 4
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
