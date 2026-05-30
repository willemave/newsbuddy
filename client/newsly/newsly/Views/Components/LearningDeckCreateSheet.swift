//
//  LearningDeckCreateSheet.swift
//  newsly
//

import SwiftUI

struct LearningDeckCreateSheet: View {
    let sourceTitle: String?
    let requiresURL: Bool
    let isSubmitting: Bool
    let onCreate: (_ url: String?, _ interestsPrompt: String?) async -> Bool

    @Environment(\.dismiss) private var dismiss
    @State private var urlText = ""
    @State private var interestsText = ""

    private var canSubmit: Bool {
        if isSubmitting {
            return false
        }
        if requiresURL {
            guard let normalizedURLText else { return false }
            return URL(string: normalizedURLText) != nil
        }
        return true
    }

    private var normalizedURLText: String? {
        nonEmptyTrimmed(urlText)
    }

    private var normalizedInterestsText: String? {
        nonEmptyTrimmed(interestsText)
    }

    private var normalizedSourceTitle: String? {
        guard let sourceTitle else { return nil }
        return nonEmptyTrimmed(sourceTitle)
    }

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Learning Deck")
                        .font(.terracottaHeadlineMedium)
                        .foregroundStyle(Color.onSurface)

                    if let sourceTitle = normalizedSourceTitle {
                        Text(sourceTitle)
                            .font(.terracottaBodyMedium)
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                if requiresURL {
                    TextField("Article, GitHub, podcast, or PDF URL", text: $urlText)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.terracottaBodyLarge)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                        .background(Color.surfaceSecondary)
                        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                }

                VStack(alignment: .leading, spacing: 8) {
                    Text("Focus")
                        .font(.terracottaBodyMedium.weight(.semibold))
                        .foregroundStyle(Color.onSurface)

                    TextEditor(text: $interestsText)
                        .font(.terracottaBodyMedium)
                        .scrollContentBackground(.hidden)
                        .frame(minHeight: 126)
                        .padding(10)
                        .background(Color.surfaceSecondary)
                        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                }

                Spacer(minLength: 0)
            }
            .padding(20)
            .background(Color.surfacePrimary.ignoresSafeArea())
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                }

                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task {
                            let didCreate = await onCreate(
                                requiresURL ? normalizedURLText : nil,
                                normalizedInterestsText
                            )
                            if didCreate {
                                dismiss()
                            }
                        }
                    } label: {
                        if isSubmitting {
                            ProgressView()
                        } else {
                            Text("Create")
                        }
                    }
                    .disabled(!canSubmit)
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    private func nonEmptyTrimmed(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
