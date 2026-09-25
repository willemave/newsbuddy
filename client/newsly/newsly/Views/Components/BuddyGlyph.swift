//
//  BuddyGlyph.swift
//  newsly
//

import SwiftUI

/// The Buddy mark drawn as vectors, traced from the `BuddyMark` artwork on its 192-unit grid.
/// Loaders use it instead of the 64pt raster so the mark stays crisp at any size and its eyes
/// can move independently of the body.
struct BuddyGlyph: View {
    /// 0 is open, 1 is closed.
    var blink: CGFloat = 0
    /// -1 looks fully left, 1 fully right.
    var glance: CGFloat = 0

    private static let grid: CGFloat = 192
    private static let clay = Color(red: 0.675, green: 0.306, blue: 0.224)
    private static let clayShade = Color(red: 0.545, green: 0.224, blue: 0.161)
    private static let pupil = Color(red: 0.522, green: 0.227, blue: 0.165)
    private static let gold = Color(red: 0.969, green: 0.820, blue: 0.443)

    private static let lensCenters = [CGPoint(x: 73, y: 70.6), CGPoint(x: 117, y: 70.6)]
    private static let lensRadius: CGFloat = 16.6
    private static let pupilRadius: CGFloat = 5.4
    private static let maxGlance: CGFloat = 5

    var body: some View {
        Canvas { context, size in
            let scale = min(size.width, size.height) / Self.grid
            context.translateBy(
                x: (size.width - Self.grid * scale) / 2,
                y: (size.height - Self.grid * scale) / 2
            )
            context.scaleBy(x: scale, y: scale)

            let body = Self.bodyPath
            context.fill(body, with: .color(Self.clay))
            context.drawLayer { layer in
                layer.clip(to: body)
                layer.fill(Self.shadePath, with: .color(Self.clayShade))
            }

            let openness = max(1 - blink, 0.16)
            let pupilWidth = Self.pupilRadius * 2 * (1 + blink * 0.5)
            let pupilHeight = Self.pupilRadius * 2 * openness
            for center in Self.lensCenters {
                let x = center.x + glance * Self.maxGlance
                context.fill(
                    Path(roundedRect: CGRect(
                        x: x - pupilWidth / 2,
                        y: center.y - pupilHeight / 2,
                        width: pupilWidth,
                        height: pupilHeight
                    ), cornerRadius: min(pupilWidth, pupilHeight) / 2),
                    with: .color(Self.pupil)
                )
            }

            context.stroke(
                Self.framesPath,
                with: .color(Self.gold),
                style: StrokeStyle(lineWidth: 4.2, lineCap: .round, lineJoin: .round)
            )
        }
    }

    /// Dome-topped bookmark with a V notch cut into its base.
    private static let bodyPath: Path = {
        var path = Path()
        path.move(to: CGPoint(x: 95, y: 134))
        path.addArc(tangent1End: CGPoint(x: 157, y: 170), tangent2End: CGPoint(x: 157, y: 70), radius: 4)
        path.addLine(to: CGPoint(x: 157, y: 70))
        path.addRelativeArc(center: CGPoint(x: 95, y: 70), radius: 62, startAngle: .zero, delta: .degrees(-180))
        path.addArc(tangent1End: CGPoint(x: 33, y: 170), tangent2End: CGPoint(x: 95, y: 134), radius: 4)
        path.closeSubpath()
        return path
    }()

    /// The fold: everything right of a curve sweeping from the crown down to the notch.
    private static let shadePath: Path = {
        var path = Path()
        path.move(to: CGPoint(x: 107, y: 0))
        path.addCurve(
            to: CGPoint(x: 95, y: 134),
            control1: CGPoint(x: 150, y: 20),
            control2: CGPoint(x: 152, y: 100)
        )
        path.addLine(to: CGPoint(x: 192, y: 192))
        path.addLine(to: CGPoint(x: 192, y: 0))
        path.closeSubpath()
        return path
    }()

    private static let framesPath: Path = {
        var path = Path()
        for center in lensCenters {
            path.addEllipse(in: CGRect(
                x: center.x - lensRadius,
                y: center.y - lensRadius,
                width: lensRadius * 2,
                height: lensRadius * 2
            ))
        }
        let bridgeY: CGFloat = 67
        path.move(to: CGPoint(x: lensCenters[0].x + lensRadius, y: bridgeY))
        path.addQuadCurve(
            to: CGPoint(x: lensCenters[1].x - lensRadius, y: bridgeY),
            control: CGPoint(x: 95, y: 61)
        )
        path.move(to: CGPoint(x: 52, y: bridgeY))
        path.addLine(to: CGPoint(x: lensCenters[0].x - lensRadius, y: bridgeY))
        path.move(to: CGPoint(x: lensCenters[1].x + lensRadius, y: bridgeY))
        path.addLine(to: CGPoint(x: 138, y: bridgeY))
        return path
    }()
}

#Preview {
    HStack(spacing: 24) {
        BuddyGlyph().frame(width: 96, height: 96)
        BuddyGlyph(blink: 1).frame(width: 96, height: 96)
        BuddyGlyph(glance: 1).frame(width: 24, height: 24)
    }
    .padding()
}
