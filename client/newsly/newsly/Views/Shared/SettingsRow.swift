//
//  SettingsRow.swift
//  newsly
//
//  Standard settings row with icon, title, subtitle, and trailing accessory.
//

import SwiftUI

// MARK: - Settings Icon

/// Plain glyph in a fixed-width slot so rows stay aligned.
///
/// Deliberately not the filled iOS Settings tile: there the tiles differ in hue and
/// aid scanning, but every row here carried the same brand color, which turned the
/// screen into a column of identical bright rectangles. `color` stays for rows that
/// carry real semantics — destructive actions in particular.
struct SettingsIcon: View {
    let systemName: String
    var color: Color = .onSurfaceSecondary

    var body: some View {
        Image(systemName: systemName)
            .font(.appSymbol(size: 17, weight: .regular))
            .foregroundStyle(color)
            .frame(width: Spacing.iconSize, height: Spacing.iconSize)
            .accessibilityHidden(true)
    }
}

// MARK: - Settings Row

struct SettingsRow<Accessory: View>: View {
    let icon: String
    let iconColor: Color
    let title: String
    var subtitle: String? = nil
    @ViewBuilder var accessory: () -> Accessory

    init(
        icon: String,
        iconColor: Color = .onSurfaceSecondary,
        title: String,
        subtitle: String? = nil,
        @ViewBuilder accessory: @escaping () -> Accessory
    ) {
        self.icon = icon
        self.iconColor = iconColor
        self.title = title
        self.subtitle = subtitle
        self.accessory = accessory
    }

    var body: some View {
        HStack(spacing: 12) {
            SettingsIcon(systemName: icon, color: iconColor)

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.listTitle)
                    .foregroundStyle(Color.onSurface)

                if let subtitle {
                    Text(subtitle)
                        .font(.listCaption)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(1)
                }
            }

            Spacer(minLength: 8)

            accessory()
        }
        .appRow(.compact)
    }
}

// MARK: - Convenience initializer for navigation rows

extension SettingsRow where Accessory == NavigationChevron {
    init(
        icon: String,
        iconColor: Color = .onSurfaceSecondary,
        title: String,
        subtitle: String? = nil
    ) {
        self.icon = icon
        self.iconColor = iconColor
        self.title = title
        self.subtitle = subtitle
        self.accessory = { NavigationChevron() }
    }
}

// MARK: - Navigation Chevron

struct NavigationChevron: View {
    var body: some View {
        Image(systemName: "chevron.right")
            .font(.appSymbol(size: 12, weight: .semibold))
            .foregroundStyle(Color.onSurfaceSecondary)
            .accessibilityHidden(true)
    }
}

// MARK: - Settings Toggle Row

struct SettingsToggleRow: View {
    let icon: String
    let iconColor: Color
    let title: String
    var subtitle: String? = nil
    @Binding var isOn: Bool

    init(
        icon: String,
        iconColor: Color = .onSurfaceSecondary,
        title: String,
        subtitle: String? = nil,
        isOn: Binding<Bool>
    ) {
        self.icon = icon
        self.iconColor = iconColor
        self.title = title
        self.subtitle = subtitle
        self._isOn = isOn
    }

    var body: some View {
        HStack(spacing: 12) {
            SettingsIcon(systemName: icon, color: iconColor)

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.listTitle)
                    .foregroundStyle(Color.onSurface)

                if let subtitle {
                    Text(subtitle)
                        .font(.listCaption)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(2)
                }
            }

            Spacer(minLength: 8)

            Toggle("", isOn: $isOn)
                .labelsHidden()
        }
        .appRow(.compact)
    }
}

#Preview {
    VStack(spacing: 0) {
        SettingsRow(icon: "books.vertical", title: "Saved")

        RowDivider()

        SettingsRow(icon: "list.bullet.rectangle", title: "Feed Sources", subtitle: "12 sources")

        RowDivider()

        SettingsToggleRow(
            icon: "eye",
            title: "Show Read Articles",
            subtitle: "Display both read and unread",
            isOn: .constant(true)
        )
    }
    .background(Color.surfacePrimary)
}
