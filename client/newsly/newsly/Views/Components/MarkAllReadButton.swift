//
//  MarkAllReadButton.swift
//  newsly
//
//  Shared "Mark All as Read" footer button for the feed tabs.
//

import SwiftUI

struct MarkAllReadButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text("Mark All as Read")
                .font(.terracottaBodyMedium.weight(.semibold))
                .foregroundStyle(Color.onSurface)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 13)
                .frame(minHeight: 44)
                .background(Color.surfaceSecondary)
                .clipShape(RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous)
                        .stroke(Color.outlineVariant.opacity(0.42), lineWidth: 1)
                }
        }
        .buttonStyle(EditorialCardButtonStyle())
    }
}

#Preview {
    MarkAllReadButton {}
        .padding()
        .background(Color.surfacePrimary)
}
