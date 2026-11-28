import SwiftUI

struct BriefingLoadingView: View {
    var body: some View {
        VStack(spacing: 0) {
            EditorialMastheadHeader(
                title: "Briefing",
                titleAccessibilityIdentifier: "briefing.screen"
            )

            BuddyLoadingView(message: "Preparing your briefing")
                .accessibilityIdentifier("briefing.loading")
        }
        .background(Color.surfacePrimary)
    }
}

struct BriefingRefreshWaitingView: View {
    var body: some View {
        HStack(spacing: 8) {
            BuddyLoadingIndicator(size: 22)
                .accessibilityHidden(true)

            Text("Refreshing briefing…")
                .font(.appCaption)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 6)
        .background(Color.surfaceSecondary)
        .accessibilityIdentifier("briefing.refresh.waiting")
    }
}

/// Dedicated child view so 1 Hz playback progress updates re-render only the
/// player bar rather than the entire `BriefingView` and pager.
struct BriefingListenPanelView: View {
    let lensKey: String
    let lensTitle: String
    let narrationController: BriefingNarrationController
    let playbackService: NarrationPlaybackService
    let isMinimized: Bool
    let onShowChapters: (BriefingNarrationChapterSheetItem) -> Void
    let onExpand: () -> Void
    @Binding var fullHeight: CGFloat
    @Binding var minimizedHeight: CGFloat

    var body: some View {
        let session = narrationController.session(for: lensKey)
        let target = narrationController.narrationEpisode(for: lensKey)
            .map { NarrationTarget.audioEpisode($0.id) }
        let isActive = session.isPreparing
            || (target != nil && target == playbackService.speakingTarget)

        if session.errorMessage != nil || isActive {
            VStack(alignment: .leading, spacing: 8) {
                if let errorMessage = session.errorMessage {
                    Text(errorMessage)
                        .font(.appCaption)
                        .foregroundStyle(Color.statusDestructive)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

                if isActive {
                    let snapshot = NarrationPlaybackSnapshot(
                        playbackService: playbackService,
                        target: target,
                        isPreparing: session.isPreparing
                    )
                    BriefingNowPlayingPanel(
                        lensTitle: lensTitle,
                        narration: session.manifest,
                        selectedIndex: session.selectedChapterIndex,
                        snapshot: snapshot,
                        isMinimized: isMinimized,
                        onTogglePlayback: {
                            Task { await narrationController.togglePlayback(for: lensKey) }
                        },
                        onPrevious: {
                            Task {
                                await narrationController.playChapter(
                                    at: session.selectedChapterIndex - 1,
                                    for: lensKey
                                )
                            }
                        },
                        onNext: {
                            Task {
                                await narrationController.playChapter(
                                    at: session.selectedChapterIndex + 1,
                                    for: lensKey
                                )
                            }
                        },
                        onShowChapters: {
                            guard let narration = session.manifest else { return }
                            onShowChapters(
                                BriefingNarrationChapterSheetItem(
                                    lensKey: lensKey,
                                    episodeGroupID: narration.episodeGroupId
                                )
                            )
                            Task {
                                await narrationController.refresh(for: lensKey)
                            }
                        },
                        onSeek: { progress in
                            guard let target else { return }
                            playbackService.seek(to: progress, for: target)
                        },
                        onSetPlaybackRate: { rate in
                            playbackService.setPlaybackRate(rate)
                        },
                        onExpand: onExpand,
                        onDismiss: { narrationController.stopPlayback(for: lensKey) },
                        fullHeight: $fullHeight,
                        minimizedHeight: $minimizedHeight
                    )
                }
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.bottom, 10)
            .onDisappear {
                fullHeight = 0
                minimizedHeight = 0
            }
        }
    }
}
