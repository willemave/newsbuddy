//
//  EmptyStateView.swift
//  newsly
//
//  Centered state views with icon, title, subtitle, and optional action.
//

import SwiftUI

struct StateView: View {
    let role: Role
    var actionTitle: String?
    var action: (() -> Void)?

    enum Role {
        case empty(icon: String, title: String, subtitle: String)
        case error(message: String)

        var icon: String {
            switch self {
            case .empty(let icon, _, _):
                return icon
            case .error:
                return "exclamationmark.triangle"
            }
        }

        var title: String {
            switch self {
            case .empty(_, let title, _):
                return title
            case .error:
                return "Error"
            }
        }

        var subtitle: String {
            switch self {
            case .empty(_, _, let subtitle):
                return subtitle
            case .error(let message):
                return message
            }
        }

        var tint: Color {
            switch self {
            case .empty:
                return Color.onSurfaceSecondary
            case .error:
                return Color.statusDestructive
            }
        }

        var isError: Bool {
            if case .error = self {
                return true
            }
            return false
        }
    }

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: role.icon)
                .font(.appSymbol(size: 40, weight: .light))
                .foregroundStyle(role.tint)

            VStack(spacing: 4) {
                Text(role.title)
                    .font(.appTitle3)
                    .foregroundStyle(Color.onSurface)
                    .multilineTextAlignment(.center)

                Text(role.subtitle)
                    .font(.appSubheadline)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 280)
            }

            if let actionTitle, let action {
                if role.isError {
                    Button(actionTitle, action: action)
                        .buttonStyle(.borderedProminent)
                        .controlSize(.small)
                        .padding(.top, 4)
                } else {
                    Button(action: action) {
                        Text(actionTitle)
                            .font(.listSubtitle.weight(.medium))
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .padding(.top, 4)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.surfacePrimary)
    }
}

struct EmptyStateView: View {
    let icon: String
    let title: String
    let subtitle: String
    var actionTitle: String? = nil
    var action: (() -> Void)? = nil

    var body: some View {
        StateView(
            role: .empty(icon: icon, title: title, subtitle: subtitle),
            actionTitle: actionTitle,
            action: action
        )
    }
}

/// Backward-compatible alias.
typealias SettingsEmptyStateView = EmptyStateView

#Preview {
    VStack {
        EmptyStateView(
            icon: "books.vertical",
            title: "No Saved Items",
            subtitle: "Bookmarks and saved knowledge will appear here",
            actionTitle: "Browse Content",
            action: {}
        )

        StateView(role: .error(message: "Unable to load content."))
    }
}
