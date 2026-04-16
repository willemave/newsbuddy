//
//  ChatEmptyState.swift
//  newsly
//

import SwiftUI

struct ChatEmptyState: View {
    let topic: String?

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "bubble.left.and.bubble.right")
                .font(.system(size: 44))
                .foregroundStyle(Color.onSurfaceSecondary.opacity(0.4))
            Text("Start the conversation")
                .font(.headline)
                .foregroundStyle(Color.onSurfaceSecondary)
            if let topic {
                Text("Topic: \(topic)")
                    .font(.subheadline)
                    .foregroundStyle(Color.topicAccent)
            }
        }
        .frame(maxWidth: .infinity)
        .multilineTextAlignment(.center)
    }
}

#if DEBUG
#Preview("Chat Empty State") {
    ChatEmptyState(topic: ChatPreviewFixtures.session.topic)
        .padding()
        .background(Color.surfacePrimary)
}
#endif
