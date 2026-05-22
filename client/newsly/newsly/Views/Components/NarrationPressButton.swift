//
//  NarrationPressButton.swift
//  newsly
//

import SwiftUI

struct NarrationPlaybackSpeedOption: Identifiable, Hashable {
    let rate: Float
    let title: String

    var id: Float { rate }

    private static let matchingTolerance: Float = 0.001

    var accessibilityActionName: String {
        "Play at \(title)"
    }

    static let standardOptions: [NarrationPlaybackSpeedOption] = [
        NarrationPlaybackSpeedOption(rate: NarrationPlaybackService.defaultPlaybackRate, title: "1x"),
        NarrationPlaybackSpeedOption(rate: 1.25, title: "1.25x"),
        NarrationPlaybackSpeedOption(rate: NarrationPlaybackService.longPressPlaybackRate, title: "1.5x"),
        NarrationPlaybackSpeedOption(rate: 2.0, title: "2x")
    ]

    static func option(
        for rate: Float,
        in options: [NarrationPlaybackSpeedOption] = standardOptions
    ) -> NarrationPlaybackSpeedOption? {
        options.first { abs($0.rate - rate) < matchingTolerance }
    }

    static func title(
        for rate: Float,
        in options: [NarrationPlaybackSpeedOption] = standardOptions
    ) -> String {
        option(for: rate, in: options)?.title ?? standardOptions[0].title
    }
}

struct NarrationPressButton<Label: View>: View {
    let isDisabled: Bool
    let accessibilityLabel: String
    let accessibilityHint: String
    let playbackSpeedOptions: [NarrationPlaybackSpeedOption]
    let onTap: () -> Void
    let onSelectPlaybackSpeed: (NarrationPlaybackSpeedOption) -> Void
    let label: () -> Label

    init(
        isDisabled: Bool = false,
        accessibilityLabel: String,
        accessibilityHint: String = "Long press to choose playback speed.",
        playbackSpeedOptions: [NarrationPlaybackSpeedOption] = NarrationPlaybackSpeedOption.standardOptions,
        onTap: @escaping () -> Void,
        onSelectPlaybackSpeed: @escaping (NarrationPlaybackSpeedOption) -> Void,
        @ViewBuilder label: @escaping () -> Label
    ) {
        self.isDisabled = isDisabled
        self.accessibilityLabel = accessibilityLabel
        self.accessibilityHint = accessibilityHint
        self.playbackSpeedOptions = playbackSpeedOptions
        self.onTap = onTap
        self.onSelectPlaybackSpeed = onSelectPlaybackSpeed
        self.label = label
    }

    var body: some View {
        accessibleButton
    }

    @ViewBuilder
    private var accessibleButton: some View {
        let baseButton = Button(action: handleTap) {
            label()
        }
        .buttonStyle(.plain)
        .disabled(isDisabled)
        .opacity(isDisabled ? 0.6 : 1)
        .contentShape(Rectangle())
        .contextMenu {
            ForEach(playbackSpeedOptions) { option in
                Button(option.title) {
                    guard !isDisabled else { return }
                    onSelectPlaybackSpeed(option)
                }
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
        .accessibilityLabel(accessibilityLabel)
        .accessibilityHint(accessibilityHint)
        .accessibilityAction {
            handleTap()
        }

        baseButton
            .accessibilityActions {
                ForEach(playbackSpeedOptions) { option in
                    Button(option.accessibilityActionName) {
                        selectPlaybackSpeed(option)
                    }
                }
            }
    }

    private func selectPlaybackSpeed(_ option: NarrationPlaybackSpeedOption) {
        guard !isDisabled else { return }
        onSelectPlaybackSpeed(option)
    }

    private func handleTap() {
        guard !isDisabled else { return }
        onTap()
    }
}
