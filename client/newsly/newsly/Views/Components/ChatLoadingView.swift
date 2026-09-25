//
//  ChatLoadingView.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import SwiftUI

struct ChatLoadingView: View {
    var body: some View {
        VStack(spacing: 14) {
            BuddyLoadingIndicator(size: 64)

            Text("Loading conversation")
                .font(.appSubheadline)
                .foregroundColor(Color.onSurfaceSecondary)
        }
    }
}

#Preview {
    ChatLoadingView()
}
