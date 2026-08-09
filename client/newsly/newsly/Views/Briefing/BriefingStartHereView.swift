import SwiftUI

struct BriefingStartHereView: View {
    let progress: APIBriefingFirstRunProgress
    let scrollToTopRequest: Int
    let onRefresh: () async -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var appeared = false

    private static let topAnchor = "briefing.start_here.top"

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Text("Your sources become one briefing.")
                        .font(.appTitle)
                        .foregroundStyle(Color.onSurface)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.bottom, 14)

                    Text("Newsbuddy reads across the sources you chose, connects different coverage of the same story, and writes the useful context into one briefing. Categories appear as patterns emerge, then keep updating as new reporting comes in.")
                        .font(.appBody)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineSpacing(5)
                        .fixedSize(horizontal: false, vertical: true)

                    Rectangle()
                        .fill(Color.outlineVariant.opacity(0.7))
                        .frame(height: 0.5)
                        .padding(.vertical, 28)

                VStack(alignment: .leading, spacing: 16) {
                    Text(headlineText)
                        .font(.appTitle3)
                        .foregroundStyle(Color.onSurface)
                        .monospacedDigit()
                        .fixedSize(horizontal: false, vertical: true)
                        .contentTransition(.interpolate)

                    if !sourceChips.isEmpty {
                        FlowLayout(spacing: 8) {
                            ForEach(sourceChips) { chip in
                                BriefingSourceChipView(chip: chip, reduceMotion: reduceMotion)
                            }
                        }
                    }

                    if let narration = narrationText {
                        Text(narration)
                            .font(.appCallout)
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .lineSpacing(4)
                            .fixedSize(horizontal: false, vertical: true)
                            .contentTransition(.interpolate)
                    }
                }
                .animation(
                    AppMotion.respectingReduceMotion(reduceMotion, AppMotion.subtle),
                    value: progress.revision
                )
                .accessibilityIdentifier("briefing.start_here.progress")

                BriefingStartHereGuide()
                    .padding(.top, 36)

                if !progress.readyCategoryKeys.isEmpty {
                    Text(readyText)
                        .font(.appCallout)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineSpacing(4)
                        .padding(.top, 24)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                        .accessibilityIdentifier("briefing.start_here.ready")
                }

                    Spacer(minLength: 48)
                }
                .id(Self.topAnchor)
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.top, 30)
                .frame(maxWidth: 680, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
                .opacity(appeared || reduceMotion ? 1 : 0)
                .offset(y: appeared || reduceMotion ? 0 : 12)
            }
            .refreshable {
                await onRefresh()
            }
            .scrollsToTopOnRequest(
                scrollToTopRequest,
                anchor: Self.topAnchor,
                using: proxy
            )
        }
        .onAppear {
            withAnimation(AppMotion.respectingReduceMotion(reduceMotion, AppMotion.subtle)) {
                appeared = true
            }
        }
        .accessibilityIdentifier("briefing.start_here")
    }

    private var headlineText: String {
        if progress.connectedSourceCount == 0 {
            return "We have your interests and are preparing the first edition."
        }
        switch progress.phase {
        case .active:
            let noun = progress.connectedSourceCount == 1 ? "source" : "sources"
            return "We connected \(progress.connectedSourceCount) \(noun)."
        case .waiting_for_content:
            return "The first pass is complete."
        case .ready:
            return "Your first edition is ready."
        }
    }

    private var narrationText: String? {
        if !progress.activeSources.isEmpty {
            let sourceList = ListFormatter.localizedString(byJoining: progress.activeSources)
            return "Now reading \(sourceList)…"
        }
        switch progress.phase {
        case .waiting_for_content:
            return "We’re shaping the first stories into categories now…"
        case .ready:
            // The ready block below the guide carries the call to action.
            return unavailableNote
        case .active:
            return unavailableNote
        }
    }

    /// Sources that couldn't be read keep a dimmed chip; this line explains it
    /// once instead of alarming per-chip copy.
    private var unavailableNote: String? {
        let unavailable = progress.completedSources
            .filter { $0.outcome == .unavailable }
            .map(\.displayName)
        guard !unavailable.isEmpty else { return nil }
        let sourceList = ListFormatter.localizedString(byJoining: unavailable)
        return "We couldn’t read \(sourceList) this time."
    }

    private var readyText: String {
        if progress.phase == .ready {
            return "Open any category above to begin. This welcome page will step aside, and your briefing will keep updating as new stories arrive."
        }
        return "A category is ready above. You can start reading it while the rest of your sources continue in the background."
    }

    /// One chip per connected source: completed first (in completion order),
    /// then the sources being read now, then the queue.
    private var sourceChips: [BriefingSourceChip] {
        var chips: [BriefingSourceChip] = []
        for source in progress.completedSources {
            chips.append(
                BriefingSourceChip(
                    name: source.displayName,
                    state: source.outcome == .unavailable
                        ? .unavailable
                        : .processed(itemCount: source.processedItemCount)
                )
            )
        }
        for name in progress.activeSources {
            chips.append(BriefingSourceChip(name: name, state: .reading))
        }
        for name in progress.queuedSources {
            chips.append(BriefingSourceChip(name: name, state: .queued))
        }
        return chips
    }
}

private struct BriefingSourceChip: Identifiable, Equatable {
    enum State: Equatable {
        case queued
        case reading
        case processed(itemCount: Int)
        case unavailable
    }

    let name: String
    let state: State

    var id: String { name }
}

private struct BriefingSourceChipView: View {
    let chip: BriefingSourceChip
    let reduceMotion: Bool

    var body: some View {
        HStack(spacing: 6) {
            BriefingSourceChipDot(state: chip.state, reduceMotion: reduceMotion)

            Text(chip.name)
                .font(.appCaption.weight(.semibold))
                .foregroundStyle(nameColor)
                .lineLimit(1)

            if case .processed(let itemCount) = chip.state, itemCount > 0 {
                Text("\(itemCount)")
                    .font(.appCaption2.weight(.bold).monospacedDigit())
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
        }
        .padding(.horizontal, 12)
        .frame(minHeight: 32)
        .background(background)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(chip.name), \(accessibilityState)")
    }

    private var nameColor: Color {
        switch chip.state {
        case .queued, .unavailable:
            return Color.onSurfaceTertiary
        case .reading, .processed:
            return Color.onSurface
        }
    }

    @ViewBuilder
    private var background: some View {
        switch chip.state {
        case .queued:
            Capsule().strokeBorder(Color.outlineVariant, lineWidth: 1)
        case .reading:
            Capsule().strokeBorder(Color.borderStrong, lineWidth: 1)
        case .processed:
            Capsule().fill(Color.surfaceTertiary)
        case .unavailable:
            Capsule().strokeBorder(
                Color.outlineVariant,
                style: StrokeStyle(lineWidth: 1, dash: [4, 3])
            )
        }
    }

    private var accessibilityState: String {
        switch chip.state {
        case .queued:
            return "waiting"
        case .reading:
            return "reading now"
        case .processed(let itemCount):
            let noun = itemCount == 1 ? "item" : "items"
            return "done, \(itemCount) \(noun)"
        case .unavailable:
            return "couldn’t be read"
        }
    }
}

private struct BriefingSourceChipDot: View {
    let state: BriefingSourceChip.State
    let reduceMotion: Bool

    @State private var pulsing = false

    var body: some View {
        Circle()
            .fill(dotColor)
            .frame(width: 6, height: 6)
            .opacity(state == .reading && pulsing ? 0.35 : 1)
            .onAppear {
                guard state == .reading, !reduceMotion else { return }
                withAnimation(.easeInOut(duration: 0.7).repeatForever(autoreverses: true)) {
                    pulsing = true
                }
            }
            .accessibilityHidden(true)
    }

    private var dotColor: Color {
        switch state {
        case .queued:
            return Color.outlineVariant
        case .reading, .processed:
            return Color.brandPrimary
        case .unavailable:
            return Color.onSurfaceTertiary.opacity(0.5)
        }
    }
}

/// The tour below the live progress: first what this screen will do, then the
/// rest of the app the user hasn't seen yet.
private struct BriefingStartHereGuide: View {
    private static let expectations = [
        BriefingStartHereFeature(
            title: "Categories appear above.",
            detail: "Each one becomes a pill beside Welcome as its stories are ready, with a count of what’s new. Open one any time — the rest keeps working."
        ),
        BriefingStartHereFeature(
            title: "Your briefing stays current.",
            detail: "New reporting folds into the same categories as it arrives, so there’s always a reason to come back."
        ),
    ]

    private static let features = [
        BriefingStartHereFeature(
            title: "Listen instead.",
            detail: "Turn any category into narration when you’re away from the screen."
        ),
        BriefingStartHereFeature(
            title: "Dig deeper.",
            detail: "Touch and hold text while reading, then choose Dig Deeper to pull fresh context from the web."
        ),
        BriefingStartHereFeature(
            title: "Save to Knowledge.",
            detail: "Keep the stories and ideas you want to remember; they live in the Knowledge tab."
        ),
        BriefingStartHereFeature(
            title: "Search Newsbuddy.",
            detail: "Find a story or detail across everything Newsbuddy has read for you."
        ),
        BriefingStartHereFeature(
            title: "Ask and learn.",
            detail: "The Learning tab turns your reading into conversations and study decks you can revisit."
        ),
        BriefingStartHereFeature(
            title: "Send links from anywhere.",
            detail: "Share an article to Newsbuddy from any app and it joins your library."
        ),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 28) {
            BriefingStartHereFeatureGroup(
                heading: "While you wait, here’s what to expect:",
                features: Self.expectations
            )
            BriefingStartHereFeatureGroup(
                heading: "And once you’re reading, Newsbuddy can also:",
                features: Self.features
            )
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("briefing.start_here.features")
    }
}

private struct BriefingStartHereFeatureGroup: View {
    let heading: String
    let features: [BriefingStartHereFeature]

    var body: some View {
        VStack(alignment: .leading, spacing: 17) {
            Text(heading)
                .font(.appCallout)
                .fontWeight(.semibold)
                .foregroundStyle(Color.onSurface)

            ForEach(features) { feature in
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    Text("•")
                        .font(.appCallout)
                        .foregroundStyle(Color.onSurfaceSecondary)

                    (Text(feature.title).fontWeight(.semibold) + Text(" \(feature.detail)"))
                        .font(.appCallout)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}

private struct BriefingStartHereFeature: Identifiable {
    let title: String
    let detail: String

    var id: String { title }
}
