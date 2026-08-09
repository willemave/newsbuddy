//
//  AssistantFeedOptionsSection.swift
//  newsly
//

import Observation
import SwiftUI

@MainActor
protocol AssistantFeedSubscribing: AnyObject {
    func subscribeFeed(
        feedURL: String,
        feedType: String,
        displayName: String?
    ) async throws -> ScraperConfig
}

extension ScraperConfigService: AssistantFeedSubscribing {}

@MainActor
@Observable
final class AssistantFeedOptionActionModel {
    private(set) var subscribedOptionIds: Set<String> = []
    private(set) var subscribingOptionIds: Set<String> = []
    private(set) var subscriptionLabels: [String: String] = [:]

    @ObservationIgnored
    private let service: any AssistantFeedSubscribing

    init(service: any AssistantFeedSubscribing = ScraperConfigService.shared) {
        self.service = service
    }

    func isSubscribed(_ option: AssistantFeedOption) -> Bool {
        option.isSubscribed || subscribedOptionIds.contains(option.id)
    }

    func isSubscribing(_ option: AssistantFeedOption) -> Bool {
        subscribingOptionIds.contains(option.id)
    }

    func subscribe(_ option: AssistantFeedOption) async {
        guard !isSubscribed(option), !isSubscribing(option) else { return }

        subscribingOptionIds.insert(option.id)
        defer { subscribingOptionIds.remove(option.id) }

        do {
            let config = try await service.subscribeFeed(
                feedURL: option.feedURL,
                feedType: option.feedType,
                displayName: option.title
            )
            subscribedOptionIds.insert(option.id)
            if config.subscriptionOutcome == .already_subscribed {
                subscriptionLabels[option.id] = "Already subscribed"
                ToastService.shared.show("Already subscribed", type: .info)
            } else if config.subscriptionOutcome == .reactivated {
                subscriptionLabels[option.id] = "Re-enabled"
                ToastService.shared.showSuccess("Re-enabled \(option.title)")
            } else {
                subscriptionLabels[option.id] = "Added"
                ToastService.shared.showSuccess("Subscribed to \(option.title)")
            }
        } catch {
            ToastService.shared.showError("Couldn't subscribe. Please try again.")
        }
    }
}

struct AssistantFeedOptionsSection: View {
    let options: [AssistantFeedOption]
    let actionModel: AssistantFeedOptionActionModel
    let onPreview: (AssistantFeedOption) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(options) { option in
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 8) {
                        Image(systemName: option.systemIcon)
                            .font(.appSymbol(size: 13, weight: .semibold))
                            .foregroundStyle(Color.brandPrimary)
                        Text(option.feedTypeLabel.uppercased())
                            .font(.appCaption2.weight(.semibold))
                            .foregroundStyle(Color.onSurfaceSecondary)
                        Text("·")
                            .font(.appCaption2)
                            .foregroundStyle(Color.onSurfaceSecondary.opacity(0.6))
                        Text(option.hostLabel)
                            .font(.appCaption2)
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .lineLimit(1)
                    }

                    Text(option.title)
                        .font(.appSubheadline.weight(.semibold))
                        .foregroundStyle(Color.onSurface)
                        .fixedSize(horizontal: false, vertical: true)

                    if let subtitle = option.subtitleText {
                        Text(subtitle)
                            .font(.appCaption)
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    actionRow(for: option)
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.surfaceTertiary)
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(Color.outlineVariant.opacity(0.5), lineWidth: 0.5)
                )
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private extension AssistantFeedOptionsSection {
    func actionRow(for option: AssistantFeedOption) -> some View {
        HStack(spacing: 8) {
            Button {
                Task { await actionModel.subscribe(option) }
            } label: {
                FeedOptionActionLabel(
                    title: addButtonTitle(for: option),
                    systemImage: actionModel.isSubscribed(option) ? "checkmark.circle.fill" : "plus.circle.fill",
                    isLoading: actionModel.isSubscribing(option)
                )
            }
            .buttonStyle(
                FeedOptionActionButtonStyle(
                    role: actionModel.isSubscribed(option) ? .subscribed : .primary
                )
            )
            .disabled(actionModel.isSubscribed(option) || actionModel.isSubscribing(option))
            .accessibilityLabel(addButtonAccessibilityLabel(for: option))
            .accessibilityIdentifier("chat.feed.subscribe.\(option.id)")

            Button {
                onPreview(option)
            } label: {
                FeedOptionActionLabel(
                    title: "View",
                    systemImage: "safari",
                    isLoading: false
                )
            }
            .buttonStyle(FeedOptionActionButtonStyle(role: .secondary))
            .accessibilityLabel("View \(option.title)")
            .accessibilityIdentifier("chat.feed.view.\(option.id)")
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 2)
    }

    func addButtonTitle(for option: AssistantFeedOption) -> String {
        if actionModel.isSubscribing(option) {
            return "Adding"
        }
        if actionModel.isSubscribed(option) {
            return actionModel.subscriptionLabels[option.id]
                ?? (option.isSubscribed ? "Already subscribed" : "Added")
        }
        return "Add"
    }

    func addButtonAccessibilityLabel(for option: AssistantFeedOption) -> String {
        if actionModel.isSubscribing(option) {
            return "Adding \(option.title)"
        }
        if actionModel.isSubscribed(option) {
            let status = actionModel.subscriptionLabels[option.id]
                ?? (option.isSubscribed ? "Already subscribed" : "Added")
            return "\(status) \(option.title)"
        }
        return "Add \(option.title)"
    }
}

private struct FeedOptionActionLabel: View {
    let title: String
    let systemImage: String
    let isLoading: Bool
    var loadingTint: Color = .chatUserBubbleText

    var body: some View {
        HStack(spacing: 7) {
            if isLoading {
                ProgressView()
                    .controlSize(.small)
                    .tint(loadingTint)
            } else {
                Image(systemName: systemImage)
                    .font(.appSymbol(size: 15, weight: .semibold))
            }

            Text(title)
                .font(.appFootnote.weight(.semibold))
                .lineLimit(1)
                .minimumScaleFactor(0.82)
        }
        .frame(maxWidth: .infinity)
    }
}

private struct FeedOptionActionButtonStyle: ButtonStyle {
    enum Role {
        case primary
        case secondary
        case subscribed
    }

    @Environment(\.isEnabled) private var isEnabled

    let role: Role

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(foregroundColor)
            .padding(.horizontal, 12)
            .frame(maxWidth: .infinity, minHeight: 46)
            .background(backgroundColor, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(borderColor, lineWidth: 0.5)
            )
            .opacity(isEnabled ? (configuration.isPressed ? 0.88 : 1) : 0.74)
            .scaleEffect(configuration.isPressed && isEnabled ? 0.96 : 1)
            .animation(AppMotion.press, value: configuration.isPressed)
    }

    private var foregroundColor: Color {
        switch role {
        case .primary:
            return .chatUserBubbleText
        case .secondary:
            return .onSurface
        case .subscribed:
            return .onSurfaceSecondary
        }
    }

    private var backgroundColor: Color {
        switch role {
        case .primary:
            return .chatUserBubble
        case .secondary:
            return Color.surfaceContainerHigh.opacity(0.7)
        case .subscribed:
            return Color.surfaceContainer.opacity(0.82)
        }
    }

    private var borderColor: Color {
        switch role {
        case .primary:
            return Color.outlineVariant.opacity(0.42)
        case .secondary, .subscribed:
            return Color.outlineVariant.opacity(0.42)
        }
    }
}

#if DEBUG
#Preview("Assistant Feed Options Section") {
    AssistantFeedOptionsSection(
        options: [ChatPreviewFixtures.feedOption],
        actionModel: ChatPreviewActionModels.feedOptions(),
        onPreview: { _ in }
    )
    .padding()
    .background(Color.surfacePrimary)
}
#endif
