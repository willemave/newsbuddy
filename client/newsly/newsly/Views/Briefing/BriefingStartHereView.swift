import SwiftUI

struct BriefingStartHereView: View {
    let progress: APIBriefingFirstRunProgress

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var appeared = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Text("Your sources become one briefing.")
                    .font(.appTitle)
                    .foregroundStyle(Color.onSurface)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.bottom, 14)

                Text("Newsly reads across the sources you chose, connects different coverage of the same story, and writes the useful context into one briefing. Categories appear as patterns emerge, then keep updating as new reporting comes in.")
                    .font(.appBody)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .lineSpacing(5)
                    .fixedSize(horizontal: false, vertical: true)

                Rectangle()
                    .fill(Color.outlineVariant.opacity(0.7))
                    .frame(height: 0.5)
                    .padding(.vertical, 28)

                Text(progressText)
                    .font(.appTitle3)
                    .foregroundStyle(Color.onSurface)
                    .lineSpacing(7)
                    .monospacedDigit()
                    .fixedSize(horizontal: false, vertical: true)
                    .contentTransition(.interpolate)
                    .animation(
                        AppMotion.respectingReduceMotion(reduceMotion, AppMotion.subtle),
                        value: progress.revision
                    )
                    .accessibilityIdentifier("briefing.start_here.progress")

                BriefingStartHereFeatureList()
                    .padding(.top, 32)

                if !progress.readyCategoryKeys.isEmpty {
                    Text(readyText)
                        .font(.appCallout)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineSpacing(4)
                        .padding(.top, 22)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                        .accessibilityIdentifier("briefing.start_here.ready")
                }

                Spacer(minLength: 48)
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.top, 30)
            .frame(maxWidth: 680, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
            .opacity(appeared || reduceMotion ? 1 : 0)
            .offset(y: appeared || reduceMotion ? 0 : 12)
        }
        .onAppear {
            withAnimation(AppMotion.respectingReduceMotion(reduceMotion, AppMotion.subtle)) {
                appeared = true
            }
        }
        .accessibilityIdentifier("briefing.start_here")
    }

    private var progressText: String {
        var sentences: [String] = []
        if progress.connectedSourceCount > 0 {
            let noun = progress.connectedSourceCount == 1 ? "source" : "sources"
            sentences.append("We connected \(progress.connectedSourceCount) \(noun).")
        } else {
            sentences.append("We have your interests and are preparing the first edition.")
        }

        sentences.append(contentsOf: progress.completedSources.map(completedSourceText))

        if !progress.activeSources.isEmpty {
            sentences.append("Now reading \(joined(progress.activeSources))…")
        } else if progress.phase == .waiting_for_content {
            sentences.append("The first pass is complete. We’re shaping the first stories into categories now…")
        } else if progress.phase == .ready {
            sentences.append("Your first edition is ready.")
        }
        return sentences.joined(separator: " ")
    }

    private func completedSourceText(_ source: APIBriefingFirstRunSourceProgress) -> String {
        let noun = source.processedItemCount == 1 ? "item" : "items"
        return "\(source.displayName) is in — \(source.processedItemCount) \(noun) processed."
    }

    private var readyText: String {
        if progress.phase == .ready {
            return "Open any category above to begin. Start Here will step aside, and your briefing will keep updating as new stories arrive."
        }
        return "A category is ready above. You can start reading it while the rest of your sources continue in the background."
    }

    private func joined(_ values: [String]) -> String {
        guard let last = values.last else { return "" }
        if values.count == 1 { return last }
        return "\(values.dropLast().joined(separator: ", ")) and \(last)"
    }
}

private struct BriefingStartHereFeatureList: View {
    private let features = [
        BriefingStartHereFeature(
            title: "Save to Knowledge.",
            detail: "Keep the stories and ideas you want to remember."
        ),
        BriefingStartHereFeature(
            title: "Search Newsly.",
            detail: "Find a story or detail across everything Newsly has read."
        ),
        BriefingStartHereFeature(
            title: "Listen instead.",
            detail: "Turn your briefing into narration when you’re away from the screen."
        ),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 17) {
            Text("With Newsly, you can also:")
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
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("briefing.start_here.features")
    }
}

private struct BriefingStartHereFeature: Identifiable {
    let title: String
    let detail: String

    var id: String { title }
}
