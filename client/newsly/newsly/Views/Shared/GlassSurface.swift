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
