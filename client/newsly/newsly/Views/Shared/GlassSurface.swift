//
//  GlassSurface.swift
//  newsly
//

import SwiftUI

enum GlassSurfaceFallback {
    case none
    case tint(opacity: Double)
    case materialStroke(strokeOpacity: Double)
    case tintStroke(fillOpacity: Double, strokeOpacity: Double)
    case fillStroke(fill: Color, fillOpacity: Double, strokeOpacity: Double)
}

extension View {
    @ViewBuilder
    func glassSurface<S: Shape>(
        in shape: S,
        tint: Color,
        opacity: Double = 0.14,
        interactive: Bool = false,
        fallback: GlassSurfaceFallback
    ) -> some View {
        if #available(iOS 26, *) {
            self.glassEffect(
                .regular
                    .tint(tint.opacity(opacity))
                    .interactive(interactive),
                in: shape
            )
        } else {
            switch fallback {
            case .none:
                self
            case let .tint(fallbackOpacity):
                self.background(tint.opacity(fallbackOpacity), in: shape)
            case let .materialStroke(strokeOpacity):
                self
                    .background(.ultraThinMaterial, in: shape)
                    .overlay {
                        shape.stroke(Color.outlineVariant.opacity(strokeOpacity), lineWidth: 1)
                    }
            case let .tintStroke(fillOpacity, strokeOpacity):
                self
                    .background(tint.opacity(fillOpacity), in: shape)
                    .overlay {
                        shape.stroke(Color.outlineVariant.opacity(strokeOpacity), lineWidth: 1)
                    }
            case let .fillStroke(fill, fillOpacity, strokeOpacity):
                self
                    .background(fill.opacity(fillOpacity), in: shape)
                    .overlay {
                        shape.stroke(Color.outlineVariant.opacity(strokeOpacity), lineWidth: 1)
                    }
            }
        }
    }
}
