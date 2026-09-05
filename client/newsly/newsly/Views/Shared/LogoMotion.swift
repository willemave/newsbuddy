import Foundation
import simd

/// Frame-rate-independent motion in scene units, kept separate from the renderer.
struct LogoMotion {
    var angle = SIMD2<Float>(-0.16, -0.65)
    var angularVelocity = SIMD2<Float>.zero
    var offset = SIMD2<Float>.zero
    var velocity = SIMD2<Float>.zero
    private(set) var elapsed: Float = 0
    var isDragging = false
    private var dragOrigin = SIMD2<Float>.zero

    mutating func beginDrag() {
        dragOrigin = offset
        isDragging = true
        angularVelocity = .zero
        velocity = .zero
    }

    mutating func drag(delta: SIMD2<Float>, translation: SIMD2<Float>) {
        isDragging = true
        angularVelocity = .zero
        angle += SIMD2(delta.y, delta.x) * 0.009
        offset = simd_clamp(
            dragOrigin + SIMD2(tanh(translation.x / 180), -tanh(translation.y / 180)) * 0.22,
            SIMD2(repeating: -0.22), SIMD2(repeating: 0.22)
        )
        velocity = .zero
    }

    mutating func release(pointsPerSecond: SIMD2<Float>) {
        isDragging = false
        angularVelocity = simd_clamp(
            SIMD2(pointsPerSecond.y, pointsPerSecond.x) * 0.005,
            SIMD2(repeating: -7), SIMD2(repeating: 7)
        )
    }

    mutating func step(_ interval: Float) {
        // Cap resume/slow-frame gaps; substeps keep the spring stable at low frame rates.
        let duration = min(max(interval, 0), 1 / 15)
        let steps = max(1, Int(ceil(duration / (1 / 120))))
        let dt = duration / Float(steps)
        for _ in 0..<steps {
            elapsed += dt
            guard !isDragging else { continue }
            velocity += (-offset * 45 - velocity * 11) * dt
            offset += velocity * dt
            angle += angularVelocity * dt
            angularVelocity *= exp(-1.65 * dt)
            // Let fast flicks complete a turn before gently finding the front again.
            if simd_length(angularVelocity) < 0.45 {
                angle.x = atan2(sin(angle.x), cos(angle.x))
                angle.y = atan2(sin(angle.y), cos(angle.y))
                let rest = SIMD2<Float>(-0.08, sin(elapsed * 0.42) * 0.24)
                angle += (rest - angle) * (1 - exp(-1.8 * dt))
            }
        }
    }
}
