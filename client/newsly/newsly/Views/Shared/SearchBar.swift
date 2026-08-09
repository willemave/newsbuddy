//
//  SearchBar.swift
//  newsly
//

import SwiftUI

struct SearchBar: View {
    let placeholder: String
    @Binding var text: String
    var isLoading: Bool = false
    var onSubmit: (() -> Void)? = nil
    var onClear: (() -> Void)? = nil
    var inputAccessibilityIdentifier: String?

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass")
                .font(.listTitle)
                .foregroundColor(.onSurfaceSecondary)
                .accessibilityHidden(true)

            TextField(placeholder, text: $text)
                .font(.listTitle)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .padding(.horizontal, 4)
                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                .background(Color.surfacePrimary.opacity(0.001))
                .accessibilityLabel(placeholder)
                .accessibilityIdentifier(ifPresent: inputAccessibilityIdentifier)
                .onSubmit { onSubmit?() }

            if isLoading {
                ProgressView()
                    .controlSize(.small)
                    .frame(width: 44, height: 44)
            } else if !text.isEmpty {
                Button {
                    text = ""
                    onClear?()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.listTitle)
                        .foregroundColor(.onSurfaceSecondary)
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .contentShape(Circle())
                .accessibilityLabel("Clear search")
            }
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 14)
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.outlineVariant, lineWidth: 1)
        )
    }
}
