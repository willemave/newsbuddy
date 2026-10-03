//
//  SettingsSectionStack.swift
//  newsly
//

import SwiftUI

struct SettingsSectionStack: View {
    let authState: AuthState
    let isApprovingCLILink: Bool
    let isDeletingAccount: Bool
    let isFeedbackVisible: Bool
    let xConnection: XConnectionResponse?
    let settings: AppSettings
    let isProcessingMarkAll: Bool
    let onLinkCLI: () -> Void
    let onSignOut: () -> Void
    let onDeleteAccount: () -> Void
    let onGiveFeedback: () -> Void
    let onMarkAll: () -> Void
    let onOpenDebugMenu: () -> Void

    var body: some View {
        // Section headers own the vertical rhythm (`Spacing.sectionTop`/`sectionBottom`),
        // so the stack adds none of its own — otherwise every gap was doubled.
        VStack(spacing: 0) {
            if case .authenticated = authState, let xConnection, xConnection.needsAttention {
                XConnectionAttentionBanner(connection: xConnection)
            }
            SettingsBrandHeader()
            SettingsLibrarySection()
            SettingsAccountSection(
                authState: authState,
                isApprovingCLILink: isApprovingCLILink,
                isDeletingAccount: isDeletingAccount,
                onLinkCLI: onLinkCLI,
                onSignOut: onSignOut,
                onDeleteAccount: onDeleteAccount
            )
            SettingsFeedbackSection(
                isVisible: isFeedbackVisible,
                onGiveFeedback: onGiveFeedback
            )
            SettingsTwitterSection(authState: authState, xConnection: xConnection)
            SettingsDisplaySection(settings: settings)
            SettingsSourcesSection()
            SettingsReadStatusSection(
                isProcessing: isProcessingMarkAll,
                onMarkAll: onMarkAll
            )
            SettingsLegalSection()

            #if DEBUG && targetEnvironment(simulator)
            SettingsDebugSection(onOpenDebugMenu: onOpenDebugMenu)
            #endif
        }
        .padding(.top, 8)
        .padding(.bottom, 120)
    }
}
