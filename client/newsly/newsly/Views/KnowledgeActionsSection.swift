//
//  KnowledgeActionsSection.swift
//  newsly
//

import SwiftUI

enum HubActionID: Hashable {
    case summary
    case topComments
    case findArticles
    case findFeeds
}

struct HubAction: Identifiable {
    let id: HubActionID
    let icon: String
    let title: String
    let run: @MainActor (KnowledgeHubViewModel) async -> ChatSessionRoute?
}

struct KnowledgeActionsSection: View {
    let viewModel: KnowledgeHubViewModel
    @Binding var runningActionID: HubActionID?
    let onSelectSession: ((ChatSessionRoute) -> Void)?

    private let primaryAction = HubAction(
        id: .summary,
        icon: "doc.text.magnifyingglass",
        title: "Today's Summary",
        run: { viewModel in await viewModel.startSummaryChat() }
    )

    private let secondaryActions: [HubAction] = [
        HubAction(
            id: .topComments,
            icon: "bubble.left.and.text.bubble.right",
            title: "Top Comments",
            run: { viewModel in await viewModel.startCommentsChat() }
        ),
        HubAction(
            id: .findArticles,
            icon: "newspaper.fill",
            title: "Find Articles",
            run: { viewModel in await viewModel.startFindArticlesChat() }
        ),
        HubAction(
            id: .findFeeds,
            icon: "dot.radiowaves.left.and.right",
            title: "Find Feeds",
            run: { viewModel in await viewModel.startFindFeedsChat() }
        ),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionHeader("Actions")
            primaryActionCard

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(secondaryActions) { action in
                        secondaryActionChip(action)
                    }
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
            }
        }
        .padding(.bottom, 24)
    }

    private var primaryActionCard: some View {
        let isRunning = runningActionID == primaryAction.id

        return Button {
            startAction(primaryAction)
        } label: {
            HStack(spacing: 12) {
                actionIcon(primaryAction.icon, size: 36, iconSize: 16, isRunning: isRunning)

                VStack(alignment: .leading, spacing: 2) {
                    Text(primaryAction.title)
                        .font(.terracottaHeadlineSmall)
                        .foregroundStyle(Color.onSurface)

                    Text("The last day across your feed")
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }

                Spacer(minLength: 0)

                Image(systemName: "arrow.right")
                    .font(.appSymbol(size: 13, weight: .semibold))
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.surfaceSecondary)
            .clipShape(RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous)
                    .stroke(Color.outlineVariant.opacity(0.3), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .allowsHitTesting(!viewModel.isCreatingSession && runningActionID == nil)
        .accessibilityValue(isRunning ? "Starting" : "")
    }

    private func secondaryActionChip(_ action: HubAction) -> some View {
        let isRunning = runningActionID == action.id

        return Button {
            startAction(action)
        } label: {
            FeedActionChip(
                title: action.title,
                systemImage: action.icon,
                isLoading: isRunning
            )
        }
        .buttonStyle(EditorialCardButtonStyle())
        .allowsHitTesting(!viewModel.isCreatingSession && runningActionID == nil)
        .accessibilityValue(isRunning ? "Starting" : "")
    }

    private func actionIcon(
        _ systemName: String,
        size: CGFloat = 32,
        iconSize: CGFloat = 15,
        isRunning: Bool = false
    ) -> some View {
        ZStack {
            if isRunning {
                ProgressView()
                    .controlSize(.small)
                    .tint(Color.brandPrimary)
            } else {
                Image(systemName: systemName)
                    .font(.appSymbol(size: iconSize, weight: .semibold))
                    .foregroundColor(.brandPrimary)
            }
        }
        .frame(width: size, height: size)
        .background(Color.brandPrimary.opacity(0.14))
        .clipShape(RoundedRectangle(cornerRadius: CornerRadius.nestedControl, style: .continuous))
    }

    private func startAction(_ action: HubAction) {
        guard !viewModel.isCreatingSession, runningActionID == nil else { return }

        runningActionID = action.id
        Task { @MainActor in
            defer { runningActionID = nil }
            if let route = await action.run(viewModel) {
                onSelectSession?(route)
            }
        }
    }

    private func sectionHeader(_ title: String) -> some View {
        Text(title.uppercased())
            .kicker()
            .accessibilityLabel(title)
            .padding(.horizontal, Spacing.appHorizontalMargin)
    }
}
