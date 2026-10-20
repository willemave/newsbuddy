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
                .multilineTextAlignment(.leading)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
        // Rounded rectangle, not a capsule: a multi-line toast's half-height
        // corners would cut into its first and last lines.
        .glassEffect(.floatingChrome, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .padding(.horizontal)
    }
}

struct ToastModifier: ViewModifier {
    @State private var toastService: ToastService

    init(toastService: ToastService) {
        _toastService = State(initialValue: toastService)
    }

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
    func withToast(service: ToastService) -> some View {
        modifier(ToastModifier(toastService: service))
    }
}
