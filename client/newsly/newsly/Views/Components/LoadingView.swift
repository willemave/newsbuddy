//
//  LoadingView.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import SwiftUI

/// Shown while the session is restored at launch. The ensō paints itself in one stroke, the
/// Buddy pops up into his seat in the ring, and the wordmark settles beneath: the same mark and
/// type the landing leads with, so a signed-out launch hands straight over to it.
///
/// Startup passes through several `LoadingView`s (auth restore, session setup, onboarding
/// check), so the intro is timed from one shared clock rather than from each view's
/// appearance: a replacement view picks up mid-stroke instead of starting over, and a
/// later appearance, such as after signing in, shows the settled mark.
struct LoadingView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(paused: reduceMotion)) { timeline in
            let frame = reduceMotion
                ? LaunchFrame.settled
                : LaunchFrame(elapsed: timeline.date.timeIntervalSince(LaunchFrame.clockStart))
            VStack(spacing: 26) {
                LaunchMark(frame: frame)

                VStack(spacing: 10) {
                    Text("Newsbuddy")
                        .font(.onboardingDisplay)
                        .tracking(frame.titleTracking)
                        .foregroundColor(.onSurface)
                        .opacity(frame.title)
                        .offset(y: (1 - frame.title) * 10)

                    Text("Your quiet news companion.")
                        .font(.onboardingSubtitle)
                        .foregroundColor(.onSurfaceSecondary)
                        .opacity(frame.tagline)
                        .offset(y: (1 - frame.tagline) * 8)
                }
            }
            // Sit slightly above centre, where the landing's logo stage puts the mark.
            .offset(y: -24)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.surfacePrimary)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Newsbuddy is loading")
    }
}

/// The ensō and the Buddy on the same 220pt frame as `BrandMark`.
private struct LaunchMark: View {
    let frame: LaunchFrame

    private static let side: CGFloat = 220
    /// Centre of the ring within the frame, as fractions; the stroke is swept around it.
    private static let ringCenter = UnitPoint(x: 0.498, y: 0.472)
    /// The Buddy's body in `BrandMark`, as fractions of the frame.
    private static let buddyCenterX: CGFloat = 0.4967
    private static let buddyBottom: CGFloat = 0.9987
    private static let buddyWidth: CGFloat = 0.305

    var body: some View {
        let side = Self.side
        // BuddyGlyph's body spans 124 of its 192 units across and ends 170 units down.
        let glyphSide = side * Self.buddyWidth * 192 / 124
        let glyphCenterY = side * Self.buddyBottom - glyphSide * (170.0 / 192 - 0.5)

        ZStack {
            Image("EnsoRing")
                .resizable()
                .frame(width: side, height: side)
                .mask {
                    BrushSweep(progress: frame.stroke, center: Self.ringCenter)
                        .blur(radius: 7)
                }
                .scaleEffect(0.97 + 0.03 * frame.stroke)

            BuddyGlyph(blink: frame.blink, glance: frame.glance)
                .frame(width: glyphSide, height: glyphSide)
                .scaleEffect(frame.buddyScale, anchor: .bottom)
                .offset(y: frame.buddyDrop * side * 0.14)
                .opacity(frame.buddyOpacity)
                .position(x: side * Self.buddyCenterX, y: glyphCenterY)
        }
        .frame(width: side, height: side)
        .background {
            RadialGradient(
                colors: [Color.brandPrimary.opacity(0.10), .clear],
                center: .center, startRadius: 10, endRadius: side * 0.8
            )
            .frame(width: side * 1.6, height: side * 1.6)
            .scaleEffect(0.8 + 0.2 * frame.glow)
            .opacity(frame.glow)
        }
    }
}

/// A wedge that opens clockwise around the ring from where the brush first touches down,
/// just past the Buddy's left shoulder. Blurred, it reveals the stroke with a soft wet edge.
private struct BrushSweep: Shape {
    var progress: CGFloat
    var center: UnitPoint

    private static let startDegrees: Double = 84
    private static let sweepDegrees: Double = 372

    func path(in rect: CGRect) -> Path {
        guard progress > 0 else { return Path() }
        let origin = CGPoint(x: rect.minX + rect.width * center.x, y: rect.minY + rect.height * center.y)
        var path = Path()
        path.move(to: origin)
        path.addRelativeArc(
            center: origin,
            radius: max(rect.width, rect.height),
            startAngle: .degrees(Self.startDegrees),
            delta: .degrees(Self.sweepDegrees * Double(progress))
        )
        path.closeSubpath()
        return path
    }
}

/// One frame of the launch intro as a pure function of time since the shared clock.
private struct LaunchFrame {
    /// The first read of this starts the intro clock; every later `LoadingView` shares it.
    static let clockStart = Date()
    static let settled = LaunchFrame(elapsed: 10, idle: false)

    var glow: CGFloat = 0
    var stroke: CGFloat = 0
    var buddyOpacity: CGFloat = 0
    var buddyScale: CGFloat = 0.6
    /// 1 is fully below his seat, 0 is seated.
    var buddyDrop: CGFloat = 1
    var blink: CGFloat = 0
    var glance: CGFloat = 0
    var title: CGFloat = 0
    var titleTracking: CGFloat = 0
    var tagline: CGFloat = 0

    init(elapsed t: TimeInterval, idle: Bool = true) {
        glow = Self.easeOut(Self.progress(t, from: 0.05, over: 1.1))
        stroke = Self.brush(Self.progress(t, from: 0.1, over: 0.95))

        let pop = Self.progress(t, from: 0.8, over: 0.55)
        let settle = Self.spring(pop)
        buddyOpacity = min(pop * 4, 1)
        buddyScale = 0.6 + 0.4 * settle
        buddyDrop = 1 - settle

        let titleIn = Self.easeOut(Self.progress(t, from: 1.0, over: 0.6))
        title = titleIn
        titleTracking = 4 * (1 - titleIn)
        tagline = Self.easeOut(Self.progress(t, from: 1.2, over: 0.6))

        // A hello blink once he lands, then the same reading loop as the loading indicator.
        let hello = t - 1.55
        if hello > 0, hello < 0.18 {
            blink = sin(hello / 0.18 * .pi)
        }
        guard idle, t > 2.2 else { return }
        let pose = BuddyPose(time: t)
        let fadeIn = Self.easeOut(Self.progress(t, from: 2.2, over: 0.8))
        glance = pose.glance * fadeIn
        blink = max(blink, pose.blink)
    }

    private static func progress(_ t: TimeInterval, from start: Double, over duration: Double) -> CGFloat {
        CGFloat(min(max((t - start) / duration, 0), 1))
    }

    private static func easeOut(_ p: CGFloat) -> CGFloat {
        1 - pow(1 - p, 3)
    }

    /// A brush lands quickly, carries through the arc, and slows as the ink runs dry.
    private static func brush(_ p: CGFloat) -> CGFloat {
        p < 0.5 ? 4 * p * p * p : 1 - pow(-2 * p + 2, 3) / 2
    }

    /// A small overshoot, so he bounces into his seat rather than sliding.
    private static func spring(_ p: CGFloat) -> CGFloat {
        guard p < 1 else { return 1 }
        return 1 - exp(-5.5 * p) * cos(p * 2.4 * .pi)
    }
}

/// The Buddy replaces generic spinners in product-owned waiting states. He bobs, reads
/// across his glasses, and blinks now and then: alive without implying determinate progress.
struct BuddyLoadingIndicator: View {
    let size: CGFloat

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(size: CGFloat = 84) {
        self.size = size
    }

    /// Below this size the ground shadow and sway turn to noise, so only the eyes move.
    private var isCompact: Bool { size < 36 }

    var body: some View {
        TimelineView(.animation(paused: reduceMotion)) { timeline in
            let pose = reduceMotion ? BuddyPose.rest : BuddyPose(time: timeline.date.timeIntervalSinceReferenceDate)
            ZStack(alignment: .bottom) {
                if !isCompact {
                    // Contact shadow: tightens and darkens as he settles, spreads as he rises.
                    Ellipse()
                        .fill(Color.black.opacity(0.1 - 0.035 * pose.lift))
                        .frame(width: size * (0.5 - 0.06 * pose.lift), height: size * 0.07)
                        .blur(radius: size * 0.02)
                }

                BuddyGlyph(blink: pose.blink, glance: pose.glance)
                    .frame(width: size, height: size)
                    .rotationEffect(.degrees(isCompact ? 0 : pose.sway), anchor: .bottom)
                    .offset(y: -pose.lift * size * (isCompact ? 0.03 : 0.045))
                    .frame(maxHeight: .infinity, alignment: .top)
            }
            .frame(width: size, height: isCompact ? size : size * 0.97)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Newsbuddy is working")
    }
}

/// One frame of the Buddy's idle loop, derived from wall-clock time so every indicator on
/// screen stays in step and no state has to be started or stopped.
private struct BuddyPose {
    /// 0 resting, 1 at the top of the bob.
    var lift: CGFloat = 0
    var sway: Double = 0
    var glance: CGFloat = 0
    var blink: CGFloat = 0

    static let rest = BuddyPose()

    init() {}

    init(time: TimeInterval) {
        lift = CGFloat(0.5 - 0.5 * cos(time * 2 * .pi / 2.6))
        sway = 1.6 * sin(time * 2 * .pi / 5.2)

        // Read a line left to right, then return to the start of the next.
        let readCycle = 3.2
        let reading = time.truncatingRemainder(dividingBy: readCycle)
        if reading < 2.5 {
            glance = CGFloat(-cos(reading / 2.5 * .pi))
        } else {
            glance = CGFloat(cos((reading - 2.5) / 0.7 * .pi))
        }

        let blinkCycle = 4.3
        let blinkLength = 0.18
        let blinking = time.truncatingRemainder(dividingBy: blinkCycle)
        if blinking < blinkLength {
            blink = CGFloat(sin(blinking / blinkLength * .pi))
        }
    }
}

struct BuddyLoadingView: View {
    let message: String?

    init(message: String? = nil) {
        self.message = message
    }

    var body: some View {
        VStack(spacing: 16) {
            BuddyLoadingIndicator(size: 88)

            if let message {
                Text(message)
                    .font(.appCallout.weight(.medium))
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.surfacePrimary)
    }
}

#Preview {
    VStack(spacing: 40) {
        BuddyLoadingIndicator(size: 88)
        BuddyLoadingIndicator(size: 22)
    }
}
