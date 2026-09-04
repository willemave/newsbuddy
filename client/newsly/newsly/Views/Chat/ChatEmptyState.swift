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
                .font(.appSymbol(size: 44))
                .foregroundStyle(Color.onSurfaceTertiary)
            Text("Start the conversation")
                .font(.appHeadline)
                .foregroundStyle(Color.onSurfaceSecondary)
            if let topic {
                Text("Topic: \(topic)")
                    .font(.appSubheadline)
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
