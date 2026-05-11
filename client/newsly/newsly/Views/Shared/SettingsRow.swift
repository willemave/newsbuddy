//
//  SettingsRow.swift
//  newsly
//
//  Standard settings row with icon, title, subtitle, and trailing accessory.
//

import SwiftUI

// MARK: - Settings Icon

/// Colored rounded-square icon matching iOS Settings style.
/// Normalises visual weight across all SF Symbol glyphs.
struct SettingsIcon: View {
    let systemName: String
    let color: Color

    var body: some View {
        Image(systemName: systemName)
            .font(.system(size: 14, weight: .semibold))
            .foregroundStyle(.white)
            .frame(width: Spacing.iconSize, height: Spacing.iconSize)
            .background(color, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
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
        iconColor: Color = .accentColor,
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
        iconColor: Color = .accentColor,
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
            .font(.system(size: 12, weight: .semibold))
            .foregroundStyle(Color.onSurfaceSecondary)
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
        iconColor: Color = .accentColor,
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
        SettingsRow(icon: "books.vertical", iconColor: .yellow, title: "Saved")

        RowDivider()

        SettingsRow(icon: "list.bullet.rectangle", title: "Feed Sources", subtitle: "12 sources")

        RowDivider()

        SettingsToggleRow(
            icon: "eye",
            iconColor: .blue,
            title: "Show Read Articles",
            subtitle: "Display both read and unread",
            isOn: .constant(true)
        )
    }
    .background(Color.surfacePrimary)
}
