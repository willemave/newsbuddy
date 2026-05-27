//
//  ReaderPaletteSettingsView.swift
//  newsly
//
//  Color theme selector for the app-wide reader palette.
//

import SwiftUI

struct ReaderPaletteSettingsView: View {
    @ObservedObject private var settings = AppSettings.shared

    private var selectedPalette: ReaderPalette {
        settings.readerPalette
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                ForEach(ReaderPalette.allCases) { palette in
                    Button {
                        settings.setReaderPalette(palette)
                    } label: {
                        ReaderPaletteOptionRow(
                            palette: palette,
                            isSelected: palette == selectedPalette
                        )
                    }
                    .buttonStyle(.plain)

                    if palette != ReaderPalette.allCases.last {
                        RowDivider(leadingInset: Spacing.rowHorizontal)
                    }
                }
            }
            .settingsCard()
            .padding(.top, 16)
            .padding(.bottom, 40)
        }
        .background(Color.surfacePrimary.ignoresSafeArea())
        .toolbarBackground(Color.surfacePrimary, for: .navigationBar)
        .navigationTitle("Color Theme")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct ReaderPaletteOptionRow: View {
    let palette: ReaderPalette
    let isSelected: Bool

    var body: some View {
        HStack(spacing: 12) {
            PaletteSwatchRow(palette: palette)
                .frame(width: 76, alignment: .leading)

            VStack(alignment: .leading, spacing: 3) {
                Text(palette.displayName)
                    .font(.listTitle)
                    .foregroundStyle(Color.onSurface)

                Text(palette.summary)
                    .font(.listCaption)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .lineLimit(2)
            }

            Spacer(minLength: 8)

            if isSelected {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 20, weight: .semibold))
                    .foregroundStyle(Color.brandPrimary)
                    .accessibilityLabel("Selected")
            }
        }
        .appRow(.regular)
    }
}

private struct PaletteSwatchRow: View {
    let palette: ReaderPalette

    var body: some View {
        HStack(spacing: -4) {
            ForEach(Array(palette.swatches.enumerated()), id: \.offset) { _, color in
                Circle()
                    .fill(color)
                    .frame(width: 18, height: 18)
                    .overlay {
                        Circle()
                            .stroke(Color.borderSubtle, lineWidth: 0.75)
                    }
            }
        }
        .accessibilityHidden(true)
    }
}
