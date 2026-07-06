//
//  SettingsFeedbackSheet.swift
//  newsly
//

import SwiftUI

struct SettingsFeedbackSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var message = ""
    @State private var isSubmitting = false
    @State private var errorMessage: String?

    let onSubmit: (String) async throws -> Void

    private var trimmedMessage: String {
        message.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 16) {
                ZStack(alignment: .topLeading) {
                    TextEditor(text: $message)
                        .scrollContentBackground(.hidden)
                        .font(.appBody)
                        .foregroundStyle(Color.onSurface)
                        .frame(minHeight: 180)
                        .accessibilityLabel("Feedback message")
                        .padding(.horizontal, 12)
                        .padding(.vertical, 10)
                        .background(Color.surfaceTertiary, in: RoundedRectangle(cornerRadius: 12))

                    if message.isEmpty {
                        Text("What should we improve?")
                            .font(.appBody)
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .padding(.horizontal, 17)
                            .padding(.vertical, 18)
                            .allowsHitTesting(false)
                    }
                }

                if let errorMessage {
                    Text(errorMessage)
                        .font(.appFootnote)
                        .foregroundStyle(Color.statusDestructive)
                }

                Spacer(minLength: 0)
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.top, 20)
            .background(Color.surfacePrimary.ignoresSafeArea())
            .navigationTitle("Give Feedback")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                    .disabled(isSubmitting)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task { await submit() }
                    } label: {
                        if isSubmitting {
                            ProgressView()
                        } else {
                            Text("Submit")
                        }
                    }
                    .disabled(isSubmitting || trimmedMessage.isEmpty)
                }
            }
        }
    }

    @MainActor
    private func submit() async {
        let feedbackMessage = trimmedMessage
        guard !isSubmitting, !feedbackMessage.isEmpty else { return }
        isSubmitting = true
        errorMessage = nil
        do {
            try await onSubmit(feedbackMessage)
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
        }
        isSubmitting = false
    }
}
