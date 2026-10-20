//
//  GlassSurface.swift
//  newsly
//

import SwiftUI

extension Glass {
    /// Glass for chrome that floats over reading text (root bar, Briefing pills
    /// and player). Clear glass let body text read straight through labels;
    /// a surface tint frosts the body while keeping the refraction and rim.
    static var floatingChrome: Glass {
        .regular.tint(Color.surfacePrimary.opacity(0.55))
    }
}

extension View {
    /// Registers a near-invisible top bar so the system soft scroll-edge effect
    /// runs under the status bar, plus `height` of custom floating chrome, on
    /// screens that hide the navigation bar. The bar must render something:
    /// with `Color.clear` the system never draws the effect. It is real layout:
    /// the scroll view's top inset grows by `height`.
    func systemTopScrollEdge(height: CGFloat = 1) -> some View {
        safeAreaBar(edge: .top, spacing: 0) {
            Color.surfacePrimary.opacity(0.01)
                .frame(height: height)
                .allowsHitTesting(false)
        }
    }

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
