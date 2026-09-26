//
//  SettingsTwitterSection.swift
//  newsly
//

import SwiftUI

struct SettingsTwitterSection: View {
    let authState: AuthState
    let xConnection: XConnectionResponse?

    var body: some View {
        if case .authenticated = authState {
            VStack(alignment: .leading, spacing: 0) {
                SectionHeader(title: "Connections")

                NavigationLink {
                    TwitterSettingsView()
                } label: {
                    SettingsRow(
                        icon: "at",
                        title: "X / Twitter",
                        subtitle: xConnection?.settingsSubtitle ?? "Not connected",
                        subtitleColor: needsAttention ? .statusDestructive : .onSurfaceSecondary
                    ) {
                        HStack(spacing: 8) {
                            if needsAttention {
                                Image(systemName: "exclamationmark.circle.fill")
                                    .font(.appSymbol(size: 17))
                                    .foregroundStyle(Color.statusDestructive)
                                    .accessibilityHidden(true)
                            }
                            NavigationChevron()
                        }
                    }
                }
                .buttonStyle(.plain)
                .settingsCard()
                .accessibilityIdentifier("settings.x")
            }
        }
    }

    private var needsAttention: Bool {
        xConnection?.needsAttention == true
    }
}

/// Sits at the top of Settings because the Connections section is below the fold.
/// Only rendered while the X connection needs attention; there is nothing to dismiss.
struct XConnectionAttentionBanner: View {
    let connection: XConnectionResponse

    var body: some View {
        NavigationLink {
            TwitterSettingsView()
        } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.appSymbol(size: 15))
                    .foregroundStyle(Color.statusDestructive)
                    .padding(.top, 1)
                    .accessibilityHidden(true)

                VStack(alignment: .leading, spacing: 4) {
                    Text("X bookmark sync paused")
                        .font(.listTitle.weight(.semibold))
                        .foregroundStyle(Color.onSurface)

                    Text(message)
                        .font(.listCaption)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .fixedSize(horizontal: false, vertical: true)

                    Text("\(connection.connectActionTitle) ›")
                        .font(.listCaption.weight(.semibold))
                        .foregroundStyle(Color.brandPrimary)
                        .padding(.top, 2)
                }

                Spacer(minLength: 0)
            }
            .padding(.horizontal, Spacing.rowHorizontal)
            .padding(.vertical, Spacing.rowVertical)
            .background(
                Color.statusDestructive.opacity(0.1),
                in: RoundedRectangle(cornerRadius: 14, style: .continuous)
            )
            .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .buttonStyle(.plain)
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.bottom, 8)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("settings.xAttention")
    }

    private var message: String {
        let action = "Reconnect X to bring in new bookmarks."
        guard let lastSynced = ContentTimestampFormatter.detailMetaText(from: connection.lastSyncedAt) else {
            return action
        }
        return "Last synced \(lastSynced). \(action)"
    }
}
