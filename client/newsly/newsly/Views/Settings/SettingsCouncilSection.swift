//
//  SettingsCouncilSection.swift
//  newsly
//

import SwiftUI

struct SettingsCouncilSection: View {
    let personas: [CouncilPersona]
    @Binding var newExpertName: String
    let isSaving: Bool
    let hasUnsavedChanges: Bool
    let onAddExpert: () -> Void
    let onRemoveExpert: (Int) -> Void
    let onSave: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionHeader(title: "Council")

            VStack(alignment: .leading, spacing: 8) {
                sectionTitle
                expertList
                addExpertRow
                footerRow
            }
            .padding(.horizontal, Spacing.rowHorizontal)
            .padding(.vertical, Spacing.rowVertical)
            .settingsCard()
        }
        .id("settings.council")
        .accessibilityIdentifier("settings.council_section")
    }

    private var sectionTitle: some View {
        HStack(spacing: 12) {
            SettingsIcon(systemName: "person.3.sequence.fill", color: .brandPrimary)
                .frame(width: 36, height: 36, alignment: .leading)

            Text("Your Experts")
                .font(.listTitle)
                .foregroundStyle(Color.onSurface)

            Spacer(minLength: 8)
        }
    }

    private var expertList: some View {
        ForEach(Array(personas.enumerated()), id: \.element.id) { index, persona in
            HStack(alignment: .top, spacing: 12) {
                Circle()
                    .fill(expertColor.opacity(0.15))
                    .frame(width: 36, height: 36)
                    .overlay(
                        Text(persona.displayName.prefix(1).uppercased())
                            .font(.appSans(size: 15, weight: .semibold))
                            .foregroundStyle(expertColor)
                    )
                    .accessibilityHidden(true)

                Text(persona.displayName)
                    .font(.appBody)
                    .foregroundStyle(Color.onSurface)

                Spacer()

                Button {
                    onRemoveExpert(index)
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.appSymbol(size: 20))
                        .foregroundStyle(Color.onSurfaceSecondary.opacity(0.5))
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .contentShape(Circle())
                .accessibilityLabel("Remove \(persona.displayName)")
            }
            .padding(.vertical, 8)
            .background(Color.surfaceSecondary.opacity(0.55))
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
    }

    @ViewBuilder
    private var addExpertRow: some View {
        if personas.count < CouncilPersona.maxExperts {
            HStack(spacing: 10) {
                TextField("Add an expert…", text: $newExpertName)
                    .textFieldStyle(.plain)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .background(Color.surfaceTertiary, in: RoundedRectangle(cornerRadius: 12))
                    .submitLabel(.done)
                    .accessibilityLabel("Expert name")
                    .onSubmit(onAddExpert)

                Button(action: onAddExpert) {
                    Image(systemName: "plus.circle.fill")
                        .font(.appSymbol(size: 24))
                        .foregroundStyle(Color.brandPrimary)
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .contentShape(Circle())
                .accessibilityLabel("Add expert")
                .disabled(newExpertName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
    }

    private var footerRow: some View {
        HStack {
            Text(footerText)
                .font(.appCaption)
                .foregroundStyle(Color.onSurfaceSecondary)

            Spacer()

            Button(action: onSave) {
                Group {
                    if isSaving {
                        ProgressView()
                            .controlSize(.small)
                            .tint(.white)
                    } else {
                        Text("Save")
                            .font(.appCallout.weight(.semibold))
                    }
                }
                .foregroundStyle(.white)
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .frame(minHeight: 44)
                .background(Color.terracottaPrimary, in: RoundedRectangle(cornerRadius: 10))
            }
            .buttonStyle(.plain)
            .contentShape(Rectangle())
            .accessibilityLabel("Save experts")
            .disabled(isSaving || (!hasUnsavedChanges && pendingExpertName.isEmpty))
            .opacity((isSaving || (!hasUnsavedChanges && pendingExpertName.isEmpty)) ? 0.4 : 1.0)
        }
    }

    private var footerText: String {
        if personas.count < CouncilPersona.minExperts {
            return "Add at least \(CouncilPersona.minExperts) experts to enable council chat."
        }
        return "Tap the council button in chat to hear from your experts."
    }

    private var pendingExpertName: String {
        newExpertName.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var expertColor: Color {
        .brandPrimary
    }
}
