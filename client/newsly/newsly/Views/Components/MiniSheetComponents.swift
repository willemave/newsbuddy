//
//  MiniSheetComponents.swift
//  newsly
//

import SwiftUI

struct MiniSheetHeader: View {
    private let title: String
    private let titleAccessibilityIdentifier: String
    private let dismiss: () -> Void

    init(
        title: String,
        titleAccessibilityIdentifier: String,
        dismiss: @escaping () -> Void
    ) {
        self.title = title
        self.titleAccessibilityIdentifier = titleAccessibilityIdentifier
        self.dismiss = dismiss
    }

    var body: some View {
        VStack(spacing: 0) {
            RoundedRectangle(cornerRadius: 2.5)
                .fill(Color.outlineVariant.opacity(0.3))
                .frame(width: 36, height: 5)
                .padding(.top, 8)

            HStack {
                Text(title)
                    .font(.appTitle3)
                    .accessibilityIdentifier(titleAccessibilityIdentifier)
                Spacer()
                Button(action: dismiss) {
                    Image(systemName: "xmark")
                        .font(.appBody)
                        .fontWeight(.semibold)
                        .foregroundColor(Color.onSurfaceSecondary)
                        .frame(width: 44, height: 44)
                        .background(Color.surfaceTertiary)
                        .clipShape(Circle())
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Close sheet")
                .accessibilityIdentifier("content.sheet.close")
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.top, 14)
            .padding(.bottom, 16)
        }
    }
}

struct MiniSheetOptionRow: View {
    private let icon: String
    private let iconColor: Color
    private let title: String
    private let subtitle: String
    private let badge: String?
    private let disabled: Bool
    private let accessibilityIdentifier: String?
    private let action: () -> Void

    init(
        icon: String,
        iconColor: Color = .onSurface,
        title: String,
        subtitle: String,
        badge: String? = nil,
        disabled: Bool = false,
        accessibilityIdentifier: String? = nil,
        action: @escaping () -> Void
    ) {
        self.icon = icon
        self.iconColor = iconColor
        self.title = title
        self.subtitle = subtitle
        self.badge = badge
        self.disabled = disabled
        self.accessibilityIdentifier = accessibilityIdentifier
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                iconView

                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                        .font(.appSubheadline)
                        .fontWeight(.semibold)
                        .foregroundColor(Color.onSurface)
                    Text(subtitle)
                        .font(.appCaption)
                        .foregroundColor(Color.onSurfaceSecondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.88)
                }

                Spacer()

                if let badge {
                    Text(badge)
                        .font(.appCaption2)
                        .fontWeight(.semibold)
                        .foregroundColor(Color.onSurfaceSecondary)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 4)
                        .background(Color.surfaceTertiary)
                        .clipShape(Capsule())
                }
            }
            .miniSheetOptionSurface()
        }
        .buttonStyle(MiniSheetOptionButtonStyle())
        .disabled(disabled)
        .opacity(disabled ? 0.55 : 1)
        .accessibilityIdentifier(ifPresent: accessibilityIdentifier)
    }

    private var iconView: some View {
        Image(systemName: icon)
            .font(.appSymbol(size: 17, weight: .semibold))
            .foregroundColor(iconColor)
            .frame(width: 34, height: 34)
            .background(iconColor.opacity(0.13))
            .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
    }
}

private struct MiniSheetOptionButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
            .opacity(configuration.isPressed ? 0.82 : 1)
            .animation(AppMotion.press, value: configuration.isPressed)
    }
}

private extension View {
    func miniSheetOptionSurface() -> some View {
        self
            .padding(.vertical, 10)
            .padding(.horizontal, 12)
            .frame(minHeight: 56)
            .background(Color.surfaceSecondary)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(Color.outlineVariant.opacity(0.28), lineWidth: 0.5)
            )
    }
}
