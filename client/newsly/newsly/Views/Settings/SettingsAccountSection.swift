//
//  SettingsAccountSection.swift
//  newsly
//

import SwiftUI

struct SettingsAccountSection: View {
    let authState: AuthState
    let isApprovingCLILink: Bool
    let onLinkCLI: () -> Void
    let onSignOut: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Account")

            if case .authenticated(let user) = authState {
                VStack(spacing: 0) {
                    AccountCard(user: user)

                    RowDivider(leadingInset: Spacing.rowHorizontal)

                    Button(action: onLinkCLI) {
                        SettingsRow(
                            icon: "qrcode.viewfinder",
                            iconColor: .brandPrimary,
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
                .foregroundStyle(.white)
                .frame(width: Spacing.iconSize, height: Spacing.iconSize)
                .background(Color.terracottaPrimary, in: Circle())
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
