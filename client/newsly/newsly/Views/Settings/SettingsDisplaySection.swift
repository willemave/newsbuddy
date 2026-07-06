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
                readingExperienceRow

                RowDivider()

                textSizeRow
            }
            .settingsCard()
        }
    }

    private var textSizeRow: some View {
        VStack(spacing: 0) {
            textSizeSlider(
                icon: "textformat.size",
                iconColor: .brandPrimary,
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
                iconColor: .brandPrimary,
                title: "Content Text Size",
                value: Binding(
                    get: { Double(settings.contentTextSizeIndex) },
                    set: { settings.setContentTextSize(Int($0.rounded())) }
                ),
                range: 0...4
            )
        }
    }

    private var readingExperienceRow: some View {
        SettingsRow(
            icon: "newspaper",
            iconColor: .brandPrimary,
            title: "Reading Experience"
        ) {
            Picker(
                "Reading Experience",
                selection: Binding(
                    get: { settings.readingExperience },
                    set: { settings.setReadingExperience($0) }
                )
            ) {
                ForEach(ReadingExperience.allCases) { experience in
                    Text(experience.title).tag(experience)
                }
            }
            .pickerStyle(.segmented)
            .frame(width: 190)
            .accessibilityIdentifier("settings.reading_experience")
        }
    }

    private func textSizeSlider(
        icon: String,
        iconColor: Color,
        title: String,
        value: Binding<Double>,
        range: ClosedRange<Double>
    ) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                SettingsIcon(systemName: icon, color: iconColor)

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
                    .tint(Color.brandPrimary)
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
