//
//  SettingsDisplaySection.swift
//  newsly
//

import SwiftUI

struct SettingsDisplaySection: View {
    let settings: AppSettings
    @Environment(\.supportsOpenDisplay) private var supportsOpenDisplay

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Display", font: .modalSupporting.weight(.semibold))

            VStack(spacing: 0) {
                textSizeRow

                if supportsOpenDisplay {
                    RowDivider(leadingInset: Spacing.rowHorizontal)
                    newsColumnsRow
                }
            }
            .settingsCard()
        }
    }

    private var textSizeRow: some View {
        VStack(spacing: 0) {
            textSizeSlider(
                icon: "textformat.size",
                title: "App Text Size",
                valueLabel: AppTextSize(index: settings.appTextSizeIndex).label,
                value: Binding(
                    get: { Double(settings.appTextSizeIndex) },
                    set: { settings.setAppTextSize(Int($0.rounded())) }
                ),
                range: 0...3
            )

            RowDivider(leadingInset: Spacing.rowHorizontal)

            textSizeSlider(
                icon: "book",
                title: "Content Text Size",
                valueLabel: ContentTextSize(index: settings.contentTextSizeIndex).label,
                value: Binding(
                    get: { Double(settings.contentTextSizeIndex) },
                    set: { settings.setContentTextSize(Int($0.rounded())) }
                ),
                range: 0...4
            )
        }
    }

    /// Only on devices that open: one or two columns of News on the open
    /// display. Phones always read in one column.
    private var newsColumnsRow: some View {
        HStack(spacing: 12) {
            SettingsIcon(systemName: "rectangle.split.2x1")

            VStack(alignment: .leading, spacing: 2) {
                Text("News columns")
                    .font(.modalBody)
                    .foregroundStyle(Color.onSurface)
                Text("On the open display")
                    .font(.modalSupporting)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }

            Spacer(minLength: 8)

            Picker(
                "News columns",
                selection: Binding(
                    get: { settings.openDisplayNewsColumns },
                    set: { settings.setOpenDisplayNewsColumns($0) }
                )
            ) {
                Text("One").tag(NewsColumnPreference.single)
                Text("Two").tag(NewsColumnPreference.double)
            }
            .pickerStyle(.segmented)
            .fixedSize()
            .accessibilityIdentifier("settings.news_columns")
        }
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.vertical, Spacing.rowVertical)
    }

    /// The slider spans the full row width rather than starting at the text inset:
    /// a control that reads as "drag me end to end" should not be visually clipped
    /// by an icon gutter it has no relationship to.
    private func textSizeSlider(
        icon: String,
        title: String,
        valueLabel: String,
        value: Binding<Double>,
        range: ClosedRange<Double>
    ) -> some View {
        VStack(spacing: 10) {
            HStack(spacing: 12) {
                SettingsIcon(systemName: icon)

                Text(title)
                    .font(.modalBody)
                    .foregroundStyle(Color.onSurface)

                Spacer(minLength: 8)

                Text(valueLabel)
                    .font(.modalSupporting)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .accessibilityHidden(true)
            }

            HStack(spacing: 10) {
                Text("A")
                    .font(.appSans(size: 13, weight: .medium))
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .accessibilityHidden(true)

                Slider(value: value, in: range, step: 1)
                    .tint(Color.onSurface)
                    .accessibilityLabel(title)
                    .accessibilityValue(textSizeAccessibilityValue(value.wrappedValue, range: range))

                Text("A")
                    .font(.appSans(size: 20, weight: .medium))
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .accessibilityHidden(true)
            }
            .frame(minHeight: 44)
            .sensoryFeedback(.selection, trigger: Int(value.wrappedValue.rounded()))
        }
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.vertical, Spacing.rowVertical)
    }

    private func textSizeAccessibilityValue(_ value: Double, range: ClosedRange<Double>) -> String {
        let stepCount = Int(range.upperBound - range.lowerBound) + 1
        let clampedValue = min(max(value, range.lowerBound), range.upperBound)
        let currentStep = Int(clampedValue - range.lowerBound) + 1
        return "\(currentStep) of \(stepCount)"
    }
}
