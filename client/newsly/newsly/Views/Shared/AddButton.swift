//
//  AddButton.swift
//  newsly
//
//  Floating add button with Liquid Glass on iOS 26+.
//

import SwiftUI

struct AddButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "plus")
                .font(.appSymbol(size: 20, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 52, height: 52)
                .background(Color.brandPrimary, in: Circle())
                .appShadow(.floating)
        }
        .glassButtonIfAvailable()
    }
}

// MARK: - Glass Button Modifier

extension View {
    @ViewBuilder
    func glassButtonIfAvailable() -> some View {
        if #available(iOS 26, *) {
            self.buttonStyle(.glassProminent)
        } else {
            self
        }
    }
}

#Preview {
    ZStack {
        Color.gray.opacity(0.2)
            .ignoresSafeArea()

        VStack {
            Spacer()
            HStack {
                Spacer()
                AddButton {}
                    .padding()
            }
        }
    }
}
