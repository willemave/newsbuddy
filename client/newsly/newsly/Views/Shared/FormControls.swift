//
//  FormControls.swift
//  newsly
//

import SwiftUI

struct FormChoiceOption: Hashable, Identifiable {
    let title: String
    let value: String

    var id: String { value }
}

struct FormChoicePillGroup: View {
    let options: [FormChoiceOption]
    @Binding var selection: String
    var unselectedBackground: Color = .surfaceSecondary

    var body: some View {
        HStack(spacing: 8) {
            ForEach(options) { option in
                FormChoicePillButton(
                    option: option,
                    isSelected: selection == option.value,
                    unselectedBackground: unselectedBackground
                ) {
                    selection = option.value
                }
            }
        }
    }
}

private struct FormChoicePillButton: View {
    let option: FormChoiceOption
    let isSelected: Bool
    let unselectedBackground: Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(option.title)
                .font(.terracottaBodyMedium)
                .foregroundStyle(isSelected ? Color.brandPrimary : Color.onSurface)
                .frame(maxWidth: .infinity, minHeight: 44)
                .background(isSelected ? Color.brandPrimary.opacity(0.14) : unselectedBackground)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .stroke(isSelected ? Color.brandPrimary.opacity(0.5) : Color.outlineVariant, lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
        .accessibilityLabel(option.title)
        .accessibilityValue(isSelected ? "Selected" : "Not selected")
    }
}

struct FormTextField: View {
    let placeholder: String
    @Binding var text: String
    var keyboardType: UIKeyboardType = .default
    var textInputAutocapitalization: TextInputAutocapitalization? = nil
    var autocorrectionDisabled = false
    var backgroundColor: Color = .surfaceSecondary
    let accessibilityLabel: String

    var body: some View {
        TextField(placeholder, text: $text)
            .keyboardType(keyboardType)
            .textInputAutocapitalization(textInputAutocapitalization)
            .autocorrectionDisabled(autocorrectionDisabled)
            .padding(.horizontal, 12)
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            .background(backgroundColor)
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(Color.outlineVariant.opacity(0.5), lineWidth: 1)
            )
            .accessibilityLabel(accessibilityLabel)
    }
}
