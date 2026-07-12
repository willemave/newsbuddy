//
//  SettingsSectionStack.swift
//  newsly
//

import SwiftUI

struct SettingsSectionStack: View {
    let authState: AuthState
    let isApprovingCLILink: Bool
    let isFeedbackVisible: Bool
    let xConnection: XConnectionResponse?
    let settings: AppSettings
    let councilPersonas: [CouncilPersona]
    @Binding var newExpertName: String
    let isSavingCouncilPersonas: Bool
    let hasUnsavedCouncilPersonaEdits: Bool
    let isProcessingMarkAll: Bool
    let onLinkCLI: () -> Void
    let onSignOut: () -> Void
    let onGiveFeedback: () -> Void
    let onAddExpert: () -> Void
    let onRemoveExpert: (Int) -> Void
    let onSaveCouncilPersonas: () -> Void
    let onMarkAll: () -> Void
    let onOpenDebugMenu: () -> Void

    var body: some View {
        VStack(spacing: 24) {
            SettingsBrandHeader()
            SettingsAccountSection(
                authState: authState,
                isApprovingCLILink: isApprovingCLILink,
                onLinkCLI: onLinkCLI,
                onSignOut: onSignOut
            )
            SettingsFeedbackSection(
                isVisible: isFeedbackVisible,
                onGiveFeedback: onGiveFeedback
            )
            SettingsTwitterSection(authState: authState, xConnection: xConnection)
            SettingsDisplaySection(settings: settings)
            SettingsCouncilSection(
                personas: councilPersonas,
                newExpertName: $newExpertName,
                isSaving: isSavingCouncilPersonas,
                hasUnsavedChanges: hasUnsavedCouncilPersonaEdits,
                onAddExpert: onAddExpert,
                onRemoveExpert: onRemoveExpert,
                onSave: onSaveCouncilPersonas
            )
            SettingsSourcesSection()
            SettingsReadStatusSection(
                isProcessing: isProcessingMarkAll,
                onMarkAll: onMarkAll
            )

            #if DEBUG && targetEnvironment(simulator)
            SettingsDebugSection(onOpenDebugMenu: onOpenDebugMenu)
            #endif
        }
        .padding(.top, 8)
        .padding(.bottom, 120)
    }
}
