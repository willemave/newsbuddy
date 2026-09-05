import RealityKit
import UIKit

/// Local, deliberately sculpted geometry. Fronts, backs and bevels remain visible on a full turn.
@MainActor
struct LogoSculptureGeometry {
    private var positions: [SIMD3<Float>] = []
    private var normals: [SIMD3<Float>] = []
    private var indices: [UInt32] = []

    private mutating func triangle(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ c: SIMD3<Float>) {
        let normal = simd_normalize(simd_cross(b - a, c - a))
        let start = UInt32(positions.count)
        positions += [a, b, c]
        normals += [normal, normal, normal]
        indices += [start, start + 1, start + 2]
    }

    private mutating func quad(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ c: SIMD3<Float>, _ d: SIMD3<Float>) {
        triangle(a, b, c)
        triangle(a, c, d)
    }

    private func mesh(named name: String) throws -> MeshResource {
        var descriptor = MeshDescriptor(name: name)
        descriptor.positions = MeshBuffers.Positions(positions)
        descriptor.normals = MeshBuffers.Normals(normals)
        descriptor.textureCoordinates = MeshBuffers.TextureCoordinates(
            positions.map { SIMD2(($0.x + 1.5) / 3, (1.5 - $0.y) / 3) }
        )
        descriptor.primitives = .triangles(indices)
        return try MeshResource.generate(from: [descriptor])
    }

    static func ring() throws -> MeshResource {
        var builder = Self()
        let segments = 300
        // Many shallow ridges follow the stroke, rather than a smooth manufactured bevel.
        let lanes = 40
        var section: [SIMD2<Float>] = []
        for lane in 0...lanes {
            section.append([-0.94 + Float(lane) / Float(lanes) * 1.88, 1])
        }
        section += [[1, 0.55], [1, -0.55]]
        for lane in (0...lanes).reversed() {
            section.append([-0.94 + Float(lane) / Float(lanes) * 1.88, -1])
        }
        section += [[-1, -0.55], [-1, 0.55]]

        func point(_ segment: Int, _ edge: Int) -> SIMD3<Float> {
            let t = Float(segment) / Float(segments)
            let cross = section[edge]
            // Stagger the ends across the brush width to leave individual bristle tips.
            let tip = sin(cross.x * 47) * 0.012 + sin(cross.x * 83) * 0.006
            let sample = t + tip * pow(abs(2 * t - 1), 8)
            var p = brushPoint(sample, across: cross.x)
            let taper = brushTaper(t)
            let grain = sin(cross.x * 79 + sin(t * 19) * 0.9) * 0.0018
                + sin(cross.x * 137 + t * 11) * 0.0009
                + sin(t * 271 + cross.x * 17) * 0.001
            let face = abs(cross.y) > 0.9
            p.z = cross.y * (0.062 * taper + (face ? grain * taper : 0))
            return p
        }
        for segment in 0..<segments {
            for edge in section.indices {
                let next = (edge + 1) % section.count
                // Cross-section runs from inner to outer on the front face.
                builder.quad(point(segment, edge), point(segment, next), point(segment + 1, next), point(segment + 1, edge))
            }
        }
        for edge in 1..<(section.count - 1) {
            builder.triangle(point(0, 0), point(0, edge + 1), point(0, edge))
            builder.triangle(point(segments, 0), point(segments, edge), point(segments, edge + 1))
        }

        // Fine, separated ink trails break up both silhouettes and feather the open ends.
        // They share one mesh/material with the band, so detail adds no extra draw calls.
        for strand in 0..<16 {
            let inner = strand % 2 == 0
            let seed = Float(strand)
            let start: Float = 0.015 + Float(strand % 5) * 0.032
            let end: Float = 0.98 - Float(strand % 3) * 0.025
            let steps = 170
            func bristle(_ index: Int, _ corner: Int) -> SIMD3<Float> {
                let u = Float(index) / Float(steps)
                let t = start + (end - start) * u
                let spread = 1.015 + Float(strand / 2) * 0.019
                let across = (inner ? -spread : spread) + sin(t * 29 + seed) * 0.008
                let width = (0.0015 + Float(strand % 3) * 0.0005)
                    * (0.08 + 0.92 * pow(sin(u * .pi), 0.5))
                let angle = -Float.pi * 0.23 + t * Float.pi * 1.88
                let radial: Float = corner == 0 || corner == 3 ? -width : width
                var p = brushPoint(t, across: across)
                p += SIMD3(cos(angle) * radial, sin(angle) * radial, 0)
                p.z = corner < 2 ? 0.045 * brushTaper(t) : -0.045 * brushTaper(t)
                return p
            }
            for index in 0..<steps {
                for corner in 0..<4 {
                    let next = (corner + 1) % 4
                    builder.quad(bristle(index, corner), bristle(index, next), bristle(index + 1, next), bristle(index + 1, corner))
                }
            }
            builder.quad(bristle(0, 3), bristle(0, 2), bristle(0, 1), bristle(0, 0))
            builder.quad(bristle(steps, 0), bristle(steps, 1), bristle(steps, 2), bristle(steps, 3))
        }
        return try builder.mesh(named: "Bristled ensō")
    }

    private static func brushTaper(_ t: Float) -> Float {
        min(1, 0.035 + max(0, min(t, 1 - t)) * 11)
    }

    private static func brushPoint(_ t: Float, across: Float) -> SIMD3<Float> {
        let angle = -Float.pi * 0.23 + t * Float.pi * 1.88
        let pressure = 0.155 + 0.033 * sin(t * 7.5 - 0.6) + 0.014 * sin(t * 19)
        let edgeGrain = sin(t * 193 + across * 3) * 0.0025
            + sin(t * 397 + across * 7) * 0.0013
        let width = pressure * brushTaper(t)
        let radius = 1.13 + 0.015 * sin(angle * 3) + across * width
            + edgeGrain * pow(min(abs(across), 1), 4)
        return [cos(angle) * radius, sin(angle) * radius + 0.14, 0]
    }

    /// Dry-brush pigment follows the same curved stroke as the mesh. Short broken
    /// streaks keep the surface from reading as evenly spaced machined grooves.
    static func inkTexture() throws -> TextureResource {
        let size: CGFloat = 1024
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        let image = UIGraphicsImageRenderer(size: CGSize(width: size, height: size), format: format).image { renderer in
            let context = renderer.cgContext
            context.setFillColor(UIColor(white: 0.87, alpha: 1).cgColor)
            context.fill(CGRect(x: 0, y: 0, width: size, height: size))
            func noise(_ seed: Float) -> Float {
                let value = sin(seed * 127.1 + 311.7) * 43758.5453
                return value - floor(value)
            }
            for stroke in 0..<2100 {
                let seed = Float(stroke)
                let start = noise(seed)
                let length = 0.002 + pow(noise(seed + 81), 3) * 0.13
                let across = noise(seed + 173) * 2.2 - 1.1
                let tone = 0.55 + noise(seed + 37) * 0.45
                context.setStrokeColor(UIColor(white: CGFloat(tone), alpha: 0.7).cgColor)
                context.setLineWidth(CGFloat(0.35 + noise(seed + 229) * 1.1))
                context.setLineCap(.round)
                context.beginPath()
                for step in 0...16 {
                    let t = min(1, start + length * Float(step) / 16)
                    let p = brushPoint(t, across: across + sin(t * 35 + seed) * 0.006)
                    let point = CGPoint(x: CGFloat((p.x + 1.5) / 3) * size, y: CGFloat((1.5 - p.y) / 3) * size)
                    if step == 0 { context.move(to: point) } else { context.addLine(to: point) }
                }
                context.strokePath()
            }
        }
        // UIGraphicsImageRenderer always produces a CG-backed image.
        guard let cgImage = image.cgImage else { throw CocoaError(.coderInvalidValue) }
        return try TextureResource(image: cgImage, options: .init(semantic: .color))
    }

    static func buddy() throws -> MeshResource {
        var builder = Self()
        var outline: [SIMD2<Float>] = [[0.46, -0.61], [0.48, -0.58], [0.48, 0.1]]
        for index in 1...48 {
            let angle = Float(index) / 48 * .pi
            outline.append([cos(angle) * 0.48, 0.1 + sin(angle) * 0.48])
        }
        outline += [[-0.48, -0.58], [-0.46, -0.61], [0, -0.32]]
        let layers: [(scale: Float, z: Float)] = [(0.92, -0.15), (1, -0.09), (1, 0.09), (0.92, 0.15)]
        func point(_ index: Int, _ layer: Int) -> SIMD3<Float> {
            let p = outline[index] * layers[layer].scale
            return [p.x, p.y, layers[layer].z]
        }
        for index in outline.indices {
            let next = (index + 1) % outline.count
            builder.triangle([0, 0, 0.15], point(index, 3), point(next, 3))
            builder.triangle([0, 0, -0.15], point(next, 0), point(index, 0))
            for layer in 0..<3 {
                builder.quad(point(index, layer), point(next, layer), point(next, layer + 1), point(index, layer + 1))
            }
        }
        return try builder.mesh(named: "Buddy")
    }

    static func glasses() throws -> MeshResource {
        var builder = Self()
        let segments = 64
        let sides = 12
        func point(_ segment: Int, _ side: Int) -> SIMD3<Float> {
            let a = Float(segment) / Float(segments) * .pi * 2
            let b = Float(side) / Float(sides) * .pi * 2
            let radius = 0.145 + cos(b) * 0.023
            return [cos(a) * radius, sin(a) * radius, sin(b) * 0.023]
        }
        for segment in 0..<segments {
            for side in 0..<sides {
                builder.quad(point(segment, side), point(segment + 1, side), point(segment + 1, side + 1), point(segment, side + 1))
            }
        }
        return try builder.mesh(named: "Round glasses")
    }

    static func material(_ color: UIColor, metallic: Float = 0) -> PhysicallyBasedMaterial {
        var material = PhysicallyBasedMaterial()
        material.baseColor = .init(tint: color)
        material.roughness = .init(floatLiteral: 0.7)
        material.metallic = .init(floatLiteral: metallic)
        return material
    }
}
