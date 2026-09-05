import XCTest
import simd
@testable import newsly

final class LogoMotionTests: XCTestCase {
    func testReleaseReturnsDisplacementToCenter() {
        var motion = LogoMotion()
        motion.drag(delta: [120, -90], translation: [10_000, -10_000])
        XCTAssertLessThanOrEqual(abs(motion.offset.x), 0.22)
        XCTAssertLessThanOrEqual(abs(motion.offset.y), 0.22)
        motion.release(pointsPerSecond: [2400, 400])
        for _ in 0..<600 { motion.step(1 / 60) }
        XCTAssertLessThan(simd_length(motion.offset), 0.001)
        XCTAssertLessThan(simd_length(motion.angularVelocity), 0.001)
        XCTAssertLessThan(abs(motion.angle.y), 0.3)
        XCTAssertLessThan(abs(motion.angle.x), 0.2)
    }

    func testFlickRotatesAfterFingerLifts() {
        var motion = LogoMotion()
        motion.drag(delta: [50, 0], translation: [50, 0])
        let releasedAngle = motion.angle.y
        motion.release(pointsPerSecond: [1600, 0])
        for _ in 0..<30 { motion.step(1 / 60) }
        XCTAssertGreaterThan(motion.angle.y - releasedAngle, 1)
    }

    func testMotionDoesNotFightHeldDrag() {
        var motion = LogoMotion()
        motion.drag(delta: [80, 20], translation: [80, 20])
        let angle = motion.angle
        let offset = motion.offset
        for _ in 0..<120 { motion.step(1 / 60) }
        XCTAssertEqual(motion.angle, angle)
        XCTAssertEqual(motion.offset, offset)
    }

    func testThirtyAndOneHundredTwentyHzConverge() {
        func simulate(hz: Float) -> LogoMotion {
            var motion = LogoMotion()
            motion.drag(delta: [70, -30], translation: [70, -30])
            motion.release(pointsPerSecond: [900, -200])
            for _ in 0..<Int(hz * 4) { motion.step(1 / hz) }
            return motion
        }
        let slow = simulate(hz: 30)
        let fast = simulate(hz: 120)
        XCTAssertLessThan(simd_length(slow.angle - fast.angle), 0.001)
        XCTAssertLessThan(simd_length(slow.offset - fast.offset), 0.001)
    }

    func testCatchingSpinningLogoDoesNotSnapItsPose() {
        var motion = LogoMotion()
        motion.drag(delta: [100, 300], translation: [100, 100])
        motion.release(pointsPerSecond: [1200, 1800])
        for _ in 0..<12 { motion.step(1 / 60) }
        let angle = motion.angle
        let offset = motion.offset
        motion.beginDrag()
        motion.drag(delta: .zero, translation: .zero)
        XCTAssertEqual(motion.angle, angle)
        XCTAssertEqual(motion.offset, offset)
        XCTAssertEqual(motion.angularVelocity, .zero)
    }

    func testLongResumeIntervalIsBounded() {
        var motion = LogoMotion()
        motion.release(pointsPerSecond: [50_000, -50_000])
        motion.step(120)
        XCTAssertLessThanOrEqual(motion.elapsed, 1 / 15 + 0.0001)
        XCTAssertTrue(motion.angle.x.isFinite && motion.angle.y.isFinite)
        XCTAssertLessThanOrEqual(abs(motion.angularVelocity.y), 7)
    }
}
