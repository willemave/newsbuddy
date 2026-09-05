import OSLog
import RealityKit
import SwiftUI

/// The first-launch sculpture has its own bounded gesture surface, separate from sign-in controls.
struct InteractiveLogoView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        ZStack {
            RadialGradient(
                colors: [Color.brandPrimary.opacity(0.09), .clear],
                center: .center, startRadius: 20, endRadius: 170
            )
            Ellipse()
                .fill(Color.black.opacity(colorScheme == .dark ? 0.24 : 0.12))
                .frame(width: 130, height: 15)
                .blur(radius: 13)
                .offset(y: 133)

            if reduceMotion {
                Image("BrandMark")
                    .resizable()
                    .scaledToFit()
                    .padding(24)
            } else {
                LogoSculptureView(active: scenePhase == .active, dark: colorScheme == .dark)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Newsbuddy, inside an ensō circle")
        .accessibilityHint(reduceMotion ? "" : "Drag to turn the sculpture. Release to let it float back.")
        .accessibilityIdentifier("intro.interactiveLogo")
    }
}

private struct LogoSculptureView: UIViewRepresentable {
    let active: Bool
    let dark: Bool

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero, cameraMode: .nonAR, automaticallyConfigureSession: false)
        view.backgroundColor = .clear
        view.isOpaque = false
        view.environment.background = .color(.clear)
        view.renderOptions = [.disableMotionBlur, .disableDepthOfField, .disableCameraGrain]
        context.coordinator.install(in: view)
        return view
    }

    func updateUIView(_ view: ARView, context: Context) {
        context.coordinator.update(active: active, dark: dark)
    }

    static func dismantleUIView(_ view: ARView, coordinator: Coordinator) {
        coordinator.stop()
        view.scene.anchors.removeAll()
    }

    @MainActor
    final class Coordinator: NSObject {
        private let sculpture = Entity()
        private let buddy = Entity()
        private let eyes = Entity()
        private var ring: ModelEntity?
        private var inkTexture: TextureResource?
        private var displayLink: CADisplayLink?
        private var lastFrame: CFTimeInterval?
        private var motion = LogoMotion()
        private var lastTranslation = SIMD2<Float>.zero
        private var darkAppearance: Bool?
        private weak var view: ARView?

        func install(in view: ARView) {
            self.view = view
            let anchor = AnchorEntity(world: .zero)
            let camera = PerspectiveCamera()
            camera.camera.fieldOfViewInDegrees = 36
            camera.position = [0, 0, 5.8]
            anchor.addChild(camera)
            anchor.addChild(sculpture)

            let key = DirectionalLight()
            key.light.intensity = 3200
            key.light.color = UIColor(red: 1, green: 0.9, blue: 0.78, alpha: 1)
            key.look(at: .zero, from: [-3, 4, 5], relativeTo: nil)
            anchor.addChild(key)

            let fill = PointLight()
            fill.light.intensity = 850
            fill.light.attenuationRadius = 12
            fill.light.color = UIColor(red: 0.76, green: 0.84, blue: 1, alpha: 1)
            fill.position = [3, 1, 4]
            anchor.addChild(fill)

            let rim = PointLight()
            rim.light.intensity = 1100
            rim.light.attenuationRadius = 10
            rim.position = [-2, 2, -3]
            anchor.addChild(rim)
            view.scene.addAnchor(anchor)

            do {
                try buildSculpture()
            } catch {
                Logger(subsystem: "org.willemaw.newsly", category: "IntroLogo")
                    .error("Unable to construct logo mesh: \(error.localizedDescription, privacy: .public)")
                // A rendering failure must never block first-launch authentication.
                let image = UIImageView(image: UIImage(named: "BrandMark"))
                image.contentMode = .scaleAspectFit
                image.frame = view.bounds
                image.autoresizingMask = [.flexibleWidth, .flexibleHeight]
                view.addSubview(image)
                return
            }

            let pan = UIPanGestureRecognizer(target: self, action: #selector(pan(_:)))
            pan.maximumNumberOfTouches = 1
            view.addGestureRecognizer(pan)
            render()
        }

        private func buildSculpture() throws {
            inkTexture = try LogoSculptureGeometry.inkTexture()
            let ring = ModelEntity(mesh: try LogoSculptureGeometry.ring(), materials: [])
            self.ring = ring
            sculpture.addChild(ring)
            sculpture.addChild(buddy)
            buddy.position = [0, -0.78, 0.23]

            let clay = LogoSculptureGeometry.material(UIColor(red: 0.67, green: 0.24, blue: 0.12, alpha: 1))
            buddy.addChild(ModelEntity(mesh: try LogoSculptureGeometry.buddy(), materials: [clay]))
            let gold = LogoSculptureGeometry.material(UIColor(red: 0.96, green: 0.68, blue: 0.22, alpha: 1), metallic: 0.24)
            let glassesMesh = try LogoSculptureGeometry.glasses()
            let eyeMaterial = LogoSculptureGeometry.material(UIColor(red: 0.25, green: 0.105, blue: 0.065, alpha: 1))
            buddy.addChild(eyes)
            eyes.position = [0, 0.1, 0.175]
            for x: Float in [-0.20, 0.20] {
                let lens = ModelEntity(mesh: glassesMesh, materials: [gold])
                lens.position = [x, 0.1, 0.20]
                buddy.addChild(lens)
                let eye = ModelEntity(mesh: .generateSphere(radius: 0.052), materials: [eyeMaterial])
                eye.position = [x, 0, 0]
                eye.scale.z = 0.4
                eyes.addChild(eye)
            }
            let bridge = ModelEntity(mesh: .generateBox(width: 0.09, height: 0.035, depth: 0.035, cornerRadius: 0.017), materials: [gold])
            bridge.position = [0, 0.11, 0.20]
            buddy.addChild(bridge)
        }

        func update(active: Bool, dark: Bool) {
            if darkAppearance != dark {
                darkAppearance = dark
                let slate = dark
                    ? UIColor(red: 0.43, green: 0.53, blue: 0.66, alpha: 1)
                    : UIColor(red: 0.18, green: 0.25, blue: 0.34, alpha: 1)
                var ink = LogoSculptureGeometry.material(slate)
                ink.roughness = .init(floatLiteral: 0.9)
                if let inkTexture {
                    ink.baseColor = .init(tint: slate, texture: .init(inkTexture))
                }
                ring?.model?.materials = [ink]
            }
            guard active, ring != nil else { stop(); return }
            guard displayLink == nil else { return }
            let target = FrameTarget(self)
            let link = CADisplayLink(target: target, selector: #selector(FrameTarget.tick(_:)))
            link.preferredFrameRateRange = CAFrameRateRange(minimum: 30, maximum: 60, preferred: 60)
            link.add(to: .main, forMode: .common)
            displayLink = link
        }

        func stop() {
            displayLink?.invalidate()
            displayLink = nil
            lastFrame = nil
            motion.isDragging = false
            motion.angularVelocity = .zero
        }

        fileprivate func tick(_ link: CADisplayLink) {
            let dt = Float(lastFrame.map { link.timestamp - $0 } ?? 0)
            lastFrame = link.timestamp
            motion.step(dt)
            render()
        }

        private func render() {
            let t = motion.elapsed
            sculpture.orientation = simd_quatf(angle: motion.angle.y, axis: [0, 1, 0])
                * simd_quatf(angle: motion.angle.x, axis: [1, 0, 0])
            sculpture.position = [motion.offset.x, motion.offset.y + sin(t * 1.1) * 0.035, 0]
            // Buddy trails the ring slightly, as if suspended inside it.
            buddy.orientation = simd_quatf(angle: sin(t * 0.9) * 0.045, axis: [0, 0, 1])
            let blinkTime = t.truncatingRemainder(dividingBy: 5.4)
            let blink = max(0, 1 - abs(blinkTime - 1.1) / 0.095)
            eyes.scale.y = 1 - blink * 0.92
        }

        @objc private func pan(_ gesture: UIPanGestureRecognizer) {
            guard let view else { return }
            let p = gesture.translation(in: view)
            let translation = SIMD2<Float>(Float(p.x), Float(p.y))
            switch gesture.state {
            case .began:
                lastTranslation = .zero
                motion.beginDrag()
            case .changed:
                motion.drag(delta: translation - lastTranslation, translation: translation)
                lastTranslation = translation
                render()
            case .ended:
                let speed = gesture.velocity(in: view)
                motion.release(pointsPerSecond: [Float(speed.x), Float(speed.y)])
            case .cancelled, .failed:
                motion.release(pointsPerSecond: .zero)
            default:
                break
            }
        }
    }

    /// CADisplayLink retains its target; the target must not retain the view coordinator.
    @MainActor
    private final class FrameTarget: NSObject {
        weak var coordinator: Coordinator?
        init(_ coordinator: Coordinator) { self.coordinator = coordinator }
        @objc func tick(_ link: CADisplayLink) { coordinator?.tick(link) }
    }
}
