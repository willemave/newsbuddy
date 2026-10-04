//
//  GlassSurface.swift
//  newsly
//

import SwiftUI

extension View {
    func glassSurface<S: Shape>(
        in shape: S,
        tint: Color,
        opacity: Double = 0.14,
        interactive: Bool = false
    ) -> some View {
        glassEffect(
            .regular
                .tint(tint.opacity(opacity))
                .interactive(interactive),
            in: shape
        )
    }
}
