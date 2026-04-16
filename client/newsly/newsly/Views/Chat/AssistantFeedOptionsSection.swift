//
//  AssistantFeedOptionsSection.swift
//  newsly
//

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
final class AssistantFeedOptionActionModel: ObservableObject {
    @Published private(set) var subscribedOptionIds: Set<String> = []
    @Published private(set) var subscribingOptionIds: Set<String> = []

    private let service: any AssistantFeedSubscribing

    init(service: any AssistantFeedSubscribing = ScraperConfigService.shared) {
        self.service = service
    }

    func isSubscribed(_ option: AssistantFeedOption) -> Bool {
        subscribedOptionIds.contains(option.id)
    }

    func isSubscribing(_ option: AssistantFeedOption) -> Bool {
        subscribingOptionIds.contains(option.id)
    }

    func subscribe(_ option: AssistantFeedOption) async {
        guard !isSubscribed(option), !isSubscribing(option) else { return }

        subscribingOptionIds.insert(option.id)
        defer { subscribingOptionIds.remove(option.id) }

        do {
            _ = try await service.subscribeFeed(
                feedURL: option.feedURL,
                feedType: option.feedType,
                displayName: option.title
            )
            subscribedOptionIds.insert(option.id)
            ToastService.shared.showSuccess("Subscribed to \(option.title)")
        } catch let apiError as APIError {
            if case .httpError(let statusCode) = apiError, statusCode == 400 {
                subscribedOptionIds.insert(option.id)
                ToastService.shared.show("Already subscribed", type: .info)
                return
            }
            ToastService.shared.showError("Failed to subscribe: \(apiError.localizedDescription)")
        } catch {
            ToastService.shared.showError("Failed to subscribe: \(error.localizedDescription)")
        }
    }
}

struct AssistantFeedOptionsSection: View {
    let options: [AssistantFeedOption]
    @ObservedObject var actionModel: AssistantFeedOptionActionModel
    let onPreview: (AssistantFeedOption) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(options) { option in
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 8) {
                        Image(systemName: option.systemIcon)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(Color.terracottaPrimary)
                        Text(option.feedTypeLabel.uppercased())
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(Color.onSurfaceSecondary)
                        Text("·")
                            .font(.caption2)
                            .foregroundStyle(Color.onSurfaceSecondary.opacity(0.6))
                        Text(option.hostLabel)
                            .font(.caption2)
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .lineLimit(1)
                    }

                    Text(option.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Color.onSurface)
                        .fixedSize(horizontal: false, vertical: true)

                    if let subtitle = option.subtitleText {
                        Text(subtitle)
                            .font(.caption)
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    HStack(spacing: 10) {
                        Button {
                            Task { await actionModel.subscribe(option) }
                        } label: {
                            if actionModel.isSubscribing(option) {
                                ProgressView()
                                    .controlSize(.small)
                            } else {
                                Image(systemName: actionModel.isSubscribed(option) ? "checkmark.circle.fill" : "plus.circle.fill")
                                    .font(.system(size: 20))
                            }
                        }
                        .foregroundStyle(actionModel.isSubscribed(option) ? Color.onSurfaceSecondary : Color.chatUserBubble)
                        .disabled(actionModel.isSubscribed(option) || actionModel.isSubscribing(option))

                        Button {
                            onPreview(option)
                        } label: {
                            Image(systemName: "safari")
                                .font(.system(size: 20))
                        }
                        .foregroundStyle(Color.onSurfaceSecondary)

                        Spacer()
                    }
                }
                .padding(12)
                .background(Color.surfaceTertiary)
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(Color.outlineVariant.opacity(0.5), lineWidth: 0.5)
                )
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }
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
