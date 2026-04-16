//
//  ChatSessionToolbarContent.swift
//  newsly
//

import SwiftUI

struct ChatSessionToolbarContent: ToolbarContent {
    let session: ChatSessionSummary?
    let onOpenArticle: (String) -> Void
    let onShowHistory: (() -> Void)?
    let onSwitchProvider: (ChatModelProvider) -> Void

    var body: some ToolbarContent {
        if let session {
            ToolbarItem(placement: .topBarLeading) {
                titleContent(for: session)
            }

            if let onShowHistory {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button(action: onShowHistory) {
                        Image(systemName: "clock.arrow.circlepath")
                    }
                    .accessibilityIdentifier("knowledge.chat_history")
                }
            }

            ToolbarItem(placement: .navigationBarTrailing) {
                providerMenu(for: session)
            }
        }
    }

    @ViewBuilder
    private func titleContent(for session: ChatSessionSummary) -> some View {
        if let articleUrl = session.articleUrl {
            Button {
                onOpenArticle(articleUrl)
            } label: {
                HStack(spacing: 4) {
                    titleText(for: session)
                    Image(systemName: "arrow.up.right.square")
                        .font(.caption2)
                }
                .foregroundStyle(Color.onSurface)
            }
        } else {
            titleText(for: session)
        }
    }

    private func titleText(for session: ChatSessionSummary) -> some View {
        Text(session.displayTitle)
            .font(.subheadline)
            .fontWeight(.semibold)
            .lineLimit(1)
            .truncationMode(.tail)
    }

    private func providerMenu(for session: ChatSessionSummary) -> some View {
        Menu {
            Section {
                Text("Current: \(session.providerDisplayName)")
                    .font(.caption)
            }
            Section("Switch Model") {
                ForEach(ChatModelProvider.allCases, id: \.self) { provider in
                    Button {
                        onSwitchProvider(provider)
                    } label: {
                        Label(provider.chatDisplayName, systemImage: provider.iconName)
                    }
                    .disabled(provider.rawValue == session.llmProvider)
                }
            }
        } label: {
            providerIcon(for: session)
                .frame(width: 32, height: 32)
                .background(Color.secondary.opacity(0.1))
                .cornerRadius(8)
        }
        .disabled(session.isCouncilMode)
        .opacity(session.isCouncilMode ? 0.45 : 1)
    }

    @ViewBuilder
    private func providerIcon(for session: ChatSessionSummary) -> some View {
        if let assetName = session.providerIconAsset {
            Image(assetName)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: 22, height: 22)
        } else {
            Image(systemName: session.providerIconFallback)
                .font(.system(size: 16))
                .foregroundStyle(Color.onSurfaceSecondary)
        }
    }
}
