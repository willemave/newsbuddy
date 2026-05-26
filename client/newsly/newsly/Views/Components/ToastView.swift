//
//  ToastView.swift
//  newsly
//
//  Toast notification UI component
//

import SwiftUI

struct ToastView: View {
    let toast: ToastMessage

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: toast.type.icon)
                .foregroundColor(toast.type.color)

            Text(toast.message)
                .font(.subheadline)
                .foregroundColor(Color.onSurface)

            Spacer()
        }
        .padding()
        .background(Color.surfaceSecondary)
        .cornerRadius(12)
        .shadow(color: Color.black.opacity(0.2), radius: 8, x: 0, y: 4)
        .padding(.horizontal)
    }
}

struct ToastModifier: ViewModifier {
    @ObservedObject var toastService = ToastService.shared

    func body(content: Content) -> some View {
        ZStack(alignment: .top) {
            content

            if let toast = toastService.currentToast {
                ToastView(toast: toast)
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .animation(.spring(), value: toastService.currentToast?.id)
                    .padding(.top, 8)
                    .zIndex(999)
            }
        }
    }
}

extension View {
    func withToast() -> some View {
        modifier(ToastModifier())
    }
}
