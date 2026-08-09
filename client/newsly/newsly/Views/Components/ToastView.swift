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
                .font(.appSubheadline)
                .foregroundColor(Color.onSurface)

            Spacer()
        }
        .padding()
        .background(Color.surfaceSecondary)
        .cornerRadius(12)
        .appShadow(.floating)
        .padding(.horizontal)
    }
}

struct ToastModifier: ViewModifier {
    @State private var toastService = ToastService.shared

    func body(content: Content) -> some View {
        ZStack(alignment: .top) {
            content

            if let toast = toastService.currentToast {
                ToastView(toast: toast)
                    .allowsHitTesting(false)
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .animation(AppMotion.panel, value: toastService.currentToast?.id)
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
