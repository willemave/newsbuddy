//
//  SettingsDisplaySection.swift
//  newsly
//

import SwiftUI

struct SettingsDisplaySection: View {
    let settings: AppSettings

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Display")

            VStack(spacing: 0) {
                textSizeRow
            }
            .settingsCard()
        }
    }

    private var textSizeRow: some View {
        VStack(spacing: 0) {
            textSizeSlider(
                icon: "textformat.size",
                title: "App Text Size",
                value: Binding(
                    get: { Double(settings.appTextSizeIndex) },
                    set: { settings.setAppTextSize(Int($0.rounded())) }
                ),
                range: 0...3
            )

            RowDivider()

            textSizeSlider(
                icon: "book",
                title: "Content Text Size",
                value: Binding(
                    get: { Double(settings.contentTextSizeIndex) },
                    set: { settings.setContentTextSize(Int($0.rounded())) }
                ),
                range: 0...4
            )
        }
    }

    private func textSizeSlider(
        icon: String,
        title: String,
        value: Binding<Double>,
        range: ClosedRange<Double>
    ) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                SettingsIcon(systemName: icon)

                Text(title)
                    .font(.listTitle)
                    .foregroundStyle(Color.onSurface)

                Spacer(minLength: 8)
            }
            .padding(.horizontal, Spacing.rowHorizontal)
            .padding(.top, Spacing.rowVertical)

            HStack(spacing: 8) {
                Text("A")
                    .font(.appSans(size: 13, weight: .medium))
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .accessibilityHidden(true)

                Slider(value: value, in: range, step: 1)
                    .tint(Color.onSurface)
                    .frame(minHeight: 44)
                    .accessibilityLabel(title)
                    .accessibilityValue(textSizeAccessibilityValue(value.wrappedValue, range: range))

                Text("A")
                    .font(.appSans(size: 22, weight: .medium))
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .accessibilityHidden(true)
            }
            .padding(.leading, Spacing.rowDividerInset)
            .padding(.trailing, Spacing.rowHorizontal)
            .padding(.bottom, Spacing.rowVertical)
        }
    }

    private func textSizeAccessibilityValue(_ value: Double, range: ClosedRange<Double>) -> String {
        let stepCount = Int(range.upperBound - range.lowerBound) + 1
        let clampedValue = min(max(value, range.lowerBound), range.upperBound)
        let currentStep = Int(clampedValue - range.lowerBound) + 1
        return "\(currentStep) of \(stepCount)"
    }
}
