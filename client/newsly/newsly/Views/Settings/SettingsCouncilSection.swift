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

            VStack(alignment: .leading, spacing: 0) {
                sectionTitle
                    .frame(minHeight: RowMetrics.compactHeight)

                expertList

                VStack(alignment: .leading, spacing: 12) {
                    addExpertRow
                    footerRow
                }
                .padding(.top, 12)
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
            SettingsIcon(systemName: "person.3.sequence.fill")

            Text("Your Experts")
                .font(.listTitle)
                .foregroundStyle(Color.onSurface)

            Spacer(minLength: 8)

            Text("\(personas.count)/\(CouncilPersona.maxExperts)")
                .font(.listCaption)
                .foregroundStyle(Color.onSurfaceSecondary)
                .monospacedDigit()
                .accessibilityHidden(true)
        }
    }

    /// Plain rows on hairlines rather than nested pills: the old pill was
    /// `surfaceSecondary` on a `surfaceSecondary` card, so it never read as a
    /// container — it just pushed the names out of line with everything else.
    private var expertList: some View {
        ForEach(Array(personas.enumerated()), id: \.element.id) { index, persona in
            VStack(spacing: 0) {
                Divider()

                HStack(spacing: 12) {
                    Circle()
                        .fill(expertColor.opacity(0.12))
                        .frame(width: Spacing.iconSize, height: Spacing.iconSize)
                        .overlay(
                            Text(persona.displayName.prefix(1).uppercased())
                                .font(.appSans(size: 13, weight: .semibold))
                                .foregroundStyle(expertColor)
                        )
                        .accessibilityHidden(true)

                    Text(persona.displayName)
                        .font(.listTitle)
                        .foregroundStyle(Color.onSurface)

                    Spacer(minLength: 8)

                    Button {
                        onRemoveExpert(index)
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.appSymbol(size: 18))
                            .foregroundStyle(Color.onSurfaceSecondary.opacity(0.5))
                            .frame(width: 44, height: 44, alignment: .trailing)
                    }
                    .buttonStyle(.plain)
                    .contentShape(Rectangle())
                    .accessibilityLabel("Remove \(persona.displayName)")
                }
                .frame(minHeight: RowMetrics.compactHeight)
            }
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
                        .foregroundStyle(Color.onSurfaceSecondary)
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
                            .tint(Color.surfacePrimary)
                    } else {
                        Text("Save")
                            .font(.appCallout.weight(.semibold))
                    }
                }
                .foregroundStyle(Color.surfacePrimary)
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .frame(minHeight: 44)
                .background(Color.brandPrimary, in: RoundedRectangle(cornerRadius: 10))
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

    /// Save is this card's one accented action; the avatars stay neutral so it reads
    /// as the primary thing on screen.
    private var expertColor: Color {
        .onSurfaceSecondary
    }
}
