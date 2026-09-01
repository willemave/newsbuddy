//
//  SettingsAccountSection.swift
//  newsly
//

import SwiftUI

struct SettingsAccountSection: View {
    let authState: AuthState
    let isApprovingCLILink: Bool
    let isDeletingAccount: Bool
    let onLinkCLI: () -> Void
    let onSignOut: () -> Void
    let onDeleteAccount: () -> Void

    var body: some View {
        // Signed out there is nothing to show, so the header goes with it rather
        // than leaving a label hanging over empty space.
        if case .authenticated(let user) = authState {
            VStack(alignment: .leading, spacing: 0) {
                SectionHeader(title: "Account")

                VStack(spacing: 0) {
                    AccountCard(user: user)

                    RowDivider(leadingInset: Spacing.rowHorizontal)

                    Button(action: onLinkCLI) {
                        SettingsRow(
                            icon: "qrcode.viewfinder",
                            title: "Link CLI"
                        ) {
                            if isApprovingCLILink {
                                ProgressView()
                            } else {
                                NavigationChevron()
                            }
                        }
                    }
                    .buttonStyle(.plain)
                    .disabled(isApprovingCLILink)

                    RowDivider(leadingInset: Spacing.rowHorizontal)

                    Button(action: onSignOut) {
                        SettingsRow(
                            icon: "rectangle.portrait.and.arrow.right",
                            iconColor: .statusDestructive,
                            title: "Sign Out"
                        ) {
                            EmptyView()
                        }
                    }
                    .buttonStyle(.plain)

                    RowDivider(leadingInset: Spacing.rowHorizontal)

                    Button(action: onDeleteAccount) {
                        SettingsRow(
                            icon: "person.crop.circle.badge.minus",
                            iconColor: .statusDestructive,
                            title: "Delete Account",
                            subtitle: "Permanently remove your account and data"
                        ) {
                            if isDeletingAccount { ProgressView() } else { EmptyView() }
                        }
                    }
                    .buttonStyle(.plain)
                    .disabled(isDeletingAccount)
                }
                .settingsCard()
            }
        }
    }
}

private struct AccountCard: View {
    let user: User

    var body: some View {
        HStack(spacing: 12) {
            Text(user.email.prefix(1).uppercased())
                .font(.appSans(size: 14, weight: .semibold))
                .foregroundStyle(Color.surfacePrimary)
                .frame(width: Spacing.iconSize, height: Spacing.iconSize)
                .background(Color.brandPrimary, in: Circle())
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 2) {
                Text(user.fullName ?? user.email)
                    .font(.listTitle.weight(.medium))
                    .foregroundStyle(Color.onSurface)

                if user.fullName != nil {
                    Text(user.email)
                        .font(.listCaption)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
            }

            Spacer()
        }
        .padding(.vertical, Spacing.rowVertical)
        .padding(.horizontal, Spacing.rowHorizontal)
    }
}
