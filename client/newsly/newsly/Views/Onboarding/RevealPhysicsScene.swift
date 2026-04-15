//
//  RevealPhysicsScene.swift
//  newsly
//
//  Created by Assistant on 2/10/26.
//

import CoreGraphics
import SpriteKit
import UIKit

struct GlyphPhraseCycler {
    private let characters: [Character]
    private var index: Int = 0

    init(phrase: String) {
        let fallbackPhrase = phrase.isEmpty ? "Newsbuddy" : phrase
        self.characters = Array(fallbackPhrase)
    }

    mutating func nextCharacter(skipSpaces: Bool) -> Character {
        guard !characters.isEmpty else { return " " }

        let maxSteps = characters.count
        for _ in 0..<maxSteps {
            let character = characters[index]
            index = (index + 1) % characters.count
            if skipSpaces && character.isWhitespace {
                continue
            }
            return character
        }

        return " "
    }
}

enum SwipeImpulseModel {
    static func normalizedImpulse(distance: CGFloat, influenceRadius: CGFloat) -> CGFloat {
        guard influenceRadius > 0 else { return 0 }
        let falloff = 1 - (distance / influenceRadius)
        return max(0, min(1, falloff))
    }

    static func impulseVector(
        from touchPoint: CGPoint,
        to nodePoint: CGPoint,
        dragVelocity: CGVector,
        influenceRadius: CGFloat,
        baseForce: CGFloat
    ) -> CGVector {
        let dx = nodePoint.x - touchPoint.x
        let dy = nodePoint.y - touchPoint.y
        let distance = max(0.0001, hypot(dx, dy))
        let normalized = normalizedImpulse(distance: distance, influenceRadius: influenceRadius)
        guard normalized > 0 else { return .zero }

        let awayX = dx / distance
        let awayY = dy / distance
        let velocityScale: CGFloat = 0.00022

        return CGVector(
            dx: (awayX * baseForce + dragVelocity.dx * velocityScale) * normalized,
            dy: (awayY * baseForce + -dragVelocity.dy * velocityScale) * normalized
        )
    }
}

// MARK: - Depth Layers

private enum GlyphDepth: Int {
    case far = 0, mid = 1, near = 2

    var fontSizeRange: ClosedRange<CGFloat> {
        switch self {
        case .far:  return 13...18
        case .mid:  return 17...24
        case .near: return 23...30
        }
    }

    var alphaRange: ClosedRange<CGFloat> {
        switch self {
        case .far:  return 0.14...0.28
        case .mid:  return 0.36...0.55
        case .near: return 0.58...0.80
        }
    }

    var dampingRange: ClosedRange<CGFloat> {
        switch self {
        case .far:  return 0.25...0.45
        case .mid:  return 0.55...0.82
        case .near: return 1.05...1.55
        }
    }

    var fontNames: [String] {
        switch self {
        case .far:  return ["Baskerville", "Palatino-Roman", "Georgia"]
        case .mid:  return ["Georgia", "Baskerville-SemiBold", "Palatino-Roman"]
        case .near: return ["Georgia-Bold", "Baskerville-Bold", "Palatino-Bold"]
        }
    }

    var restitutionRange: ClosedRange<CGFloat> {
        switch self {
        case .far:  return 0.06...0.14
        case .mid:  return 0.14...0.24
        case .near: return 0.20...0.34
        }
    }

    var fadeDelay: ClosedRange<Double> {
        switch self {
        case .far:  return 2.5...4.0
        case .mid:  return 4.0...6.0
        case .near: return 5.5...7.5
        }
    }

    var swipeScale: CGFloat {
        switch self {
        case .far:  return 0.0
        case .mid:  return 0.5
        case .near: return 1.0
        }
    }

    var driftScale: CGFloat {
        switch self {
        case .far:  return 0.3
        case .mid:  return 0.65
        case .near: return 1.0
        }
    }

    static func random(using generator: inout some RandomNumberGenerator) -> GlyphDepth {
        let roll = CGFloat.random(in: 0...1, using: &generator)
        if roll < 0.40 { return .far }
        if roll < 0.72 { return .mid }
        return .near
    }
}

// MARK: - Scene

final class RevealPhysicsScene: SKScene {
    private static let wordBank: [String] = [
        // Tech
        "AI", "ML", "API", "GPU", "LLM", "RSS", "IoT", "SaaS",
        "Code", "Data", "Ship", "Beta", "Cloud", "Build", "Scale",
        "Stack", "Debug", "Deploy", "React", "Chips", "Token",
        "Linux", "Neural", "Crypto", "Quantum", "Silicon", "Infra",
        // News & media
        "News", "Brief", "Story", "Signal", "Digest", "Thread",
        "Report", "Source", "Curated", "Insight", "Trends",
        "Headline", "Summary", "Podcast", "Opinion", "Analysis",
        "Breaking", "Editorial",
        // Science
        "Climate", "Genome", "Orbit", "Fusion", "Carbon", "Cosmic",
        "Photon", "Plasma", "Species", "Stellar",
        // Business
        "Markets", "Funding", "Startup", "Revenue", "Venture",
        "Growth", "Equity", "Merger", "IPO", "Series", "Profit",
        // Culture
        "Design", "Film", "Music", "Books", "Culture", "Art",
        "Archive", "Studio", "Canvas",
        // World
        "Policy", "Summit", "Treaty", "Reform", "Global", "Trade",
        "Borders",
        // Abstract
        "Ideas", "Future", "Impact", "Change", "Vision", "Focus",
        "Edge", "Pulse", "Shift", "Wave", "Spark", "Core", "Flux",
        "Echo", "Nexus", "Scope", "Lens", "Grid", "Index",
    ]

    private let glyphNodeName = "rainGlyph"
    private let wallNodeNames = ["leftWall", "rightWall"]
    private let maxGlyphNodes = 180
    private let spawnInterval: TimeInterval = 0.18
    private let influenceRadius: CGFloat = 220
    private let baseImpulseForce: CGFloat = 1.70

    private let palette: [UIColor] = [
        UIColor(red: 0.78, green: 0.84, blue: 0.92, alpha: 1.0),
        UIColor(red: 0.68, green: 0.79, blue: 0.86, alpha: 1.0),
        UIColor(red: 0.85, green: 0.80, blue: 0.72, alpha: 1.0),
        UIColor(red: 0.72, green: 0.82, blue: 0.78, alpha: 1.0),
    ]

    private var spawnAccumulator: TimeInterval = 0
    private var lastUpdateTimestamp: TimeInterval = 0
    private var floatDriftPhase: TimeInterval = 0
    private var rainActive = true
    private var laneCount: Int = 12
    private var seededGenerator = LCG(seed: 0x5A17)

    override init(size: CGSize) {
        super.init(size: size)
        scaleMode = .resizeFill
        backgroundColor = .clear
        physicsWorld.gravity = CGVector(dx: 0.0, dy: -1.1)
    }

    required init?(coder aDecoder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func didMove(to view: SKView) {
        super.didMove(to: view)
        updateBounds(to: size)
    }

    override func didChangeSize(_ oldSize: CGSize) {
        super.didChangeSize(oldSize)
        updateBounds(to: size)
    }

    func configure(seed: UInt64, isEnabled: Bool) {
        rainActive = isEnabled
        seededGenerator = LCG(seed: seed)
        children
            .filter { $0.name == glyphNodeName }
            .forEach { $0.removeFromParent() }
    }

    func updateBounds(to newSize: CGSize) {
        guard newSize.width > 0, newSize.height > 0 else { return }
        if size != newSize {
            size = newSize
        }
        anchorPoint = .zero
        laneCount = max(6, Int(newSize.width / 50))
        configureWalls(for: newSize)
    }

    func applySwipe(at point: CGPoint, velocity: CGVector) {
        guard rainActive else { return }

        for node in children where node.name == glyphNodeName {
            let depth = GlyphDepth(rawValue: Int(node.zPosition)) ?? .mid
            guard depth.swipeScale > 0, let body = node.physicsBody else { continue }

            var impulse = SwipeImpulseModel.impulseVector(
                from: point,
                to: node.position,
                dragVelocity: velocity,
                influenceRadius: influenceRadius,
                baseForce: baseImpulseForce
            )
            impulse.dx *= depth.swipeScale
            impulse.dy *= depth.swipeScale
            guard abs(impulse.dx) > 0.001 || abs(impulse.dy) > 0.001 else { continue }

            body.isResting = false
            body.applyImpulse(impulse)
            body.applyAngularImpulse(
                CGFloat.random(in: -0.04...0.04) * max(0.15, abs(impulse.dx + impulse.dy))
            )
        }
    }

    override func update(_ currentTime: TimeInterval) {
        if lastUpdateTimestamp == 0 {
            lastUpdateTimestamp = currentTime
            return
        }

        let delta = min(0.05, max(0, currentTime - lastUpdateTimestamp))
        lastUpdateTimestamp = currentTime

        if rainActive {
            spawnAccumulator += delta
            while spawnAccumulator >= spawnInterval {
                spawnGlyph()
                spawnAccumulator -= spawnInterval
            }
        }

        floatDriftPhase += delta
        applyAmbientFloatDrift(time: floatDriftPhase)
        cleanupOffscreenGlyphs()
        trimGlyphOverflowIfNeeded()
    }

    // MARK: - Private

    private func configureWalls(for sceneSize: CGSize) {
        for name in wallNodeNames {
            childNode(withName: name)?.removeFromParent()
        }

        let left = SKNode()
        left.name = wallNodeNames[0]
        left.physicsBody = SKPhysicsBody(edgeFrom: CGPoint(x: 0, y: -64), to: CGPoint(x: 0, y: sceneSize.height + 64))
        left.physicsBody?.isDynamic = false
        addChild(left)

        let right = SKNode()
        right.name = wallNodeNames[1]
        right.physicsBody = SKPhysicsBody(edgeFrom: CGPoint(x: sceneSize.width, y: -64), to: CGPoint(x: sceneSize.width, y: sceneSize.height + 64))
        right.physicsBody?.isDynamic = false
        addChild(right)
    }

    private func spawnGlyph() {
        guard size.width > 0, size.height > 0, laneCount > 0 else { return }

        let depth = GlyphDepth.random(using: &seededGenerator)

        let laneWidth = size.width / CGFloat(laneCount)
        let lane = Int.random(in: 0..<laneCount, using: &seededGenerator)
        let xJitter = CGFloat.random(in: -4...4, using: &seededGenerator)
        let x = (CGFloat(lane) + 0.5) * laneWidth + xJitter
        let y = size.height + CGFloat.random(in: 20...80, using: &seededGenerator)

        let word = Self.wordBank.randomElement(using: &seededGenerator) ?? "News"
        let fontName = depth.fontNames.randomElement(using: &seededGenerator) ?? "AvenirNext-Medium"
        let fontSize = CGFloat.random(in: depth.fontSizeRange, using: &seededGenerator)
        let font = UIFont(name: fontName, size: fontSize) ?? UIFont.systemFont(ofSize: fontSize, weight: .medium)

        let label = SKLabelNode(fontNamed: font.fontName)
        label.name = glyphNodeName
        label.text = word
        label.fontSize = fontSize
        label.fontColor = palette.randomElement(using: &seededGenerator) ?? .white
        label.position = CGPoint(x: x, y: y)
        label.horizontalAlignmentMode = .center
        label.verticalAlignmentMode = .center
        label.zRotation = CGFloat.random(in: -0.04...0.04, using: &seededGenerator)
        label.zPosition = CGFloat(depth.rawValue)

        // Start invisible, fade in
        let targetAlpha = CGFloat.random(in: depth.alphaRange, using: &seededGenerator)
        label.alpha = 0
        addChild(label)

        let fadeDelay = TimeInterval.random(in: depth.fadeDelay, using: &seededGenerator)
        label.run(.sequence([
            .fadeAlpha(to: targetAlpha, duration: 0.6),
            .wait(forDuration: fadeDelay),
            .fadeAlpha(to: targetAlpha * 0.35, duration: 2.2),
        ]))

        let sizeEstimate = NSString(string: word).size(withAttributes: [.font: font])
        let body = SKPhysicsBody(
            rectangleOf: CGSize(
                width: max(18, sizeEstimate.width * 1.08),
                height: max(22, sizeEstimate.height * 1.08)
            )
        )
        body.affectedByGravity = true
        body.allowsRotation = true
        body.restitution = CGFloat.random(in: depth.restitutionRange, using: &seededGenerator)
        body.friction = CGFloat.random(in: 0.20...0.36, using: &seededGenerator)
        body.linearDamping = CGFloat.random(in: depth.dampingRange, using: &seededGenerator)
        body.angularDamping = CGFloat.random(in: 0.58...0.92, using: &seededGenerator)
        body.mass = max(0.030, (sizeEstimate.width * sizeEstimate.height) / 7_800)
        body.usesPreciseCollisionDetection = depth == .near
        body.velocity = CGVector(
            dx: CGFloat.random(in: -3...3, using: &seededGenerator),
            dy: CGFloat.random(in: -2...1, using: &seededGenerator)
        )
        label.physicsBody = body
    }

    private func applyAmbientFloatDrift(time: TimeInterval) {
        let t = CGFloat(time)
        for node in children where node.name == glyphNodeName {
            guard let body = node.physicsBody else { continue }
            let depth = GlyphDepth(rawValue: Int(node.zPosition)) ?? .mid
            let scale = depth.driftScale

            let px = node.position.x
            let py = node.position.y

            let sway = sin(py * 0.018 + t * 1.6) * 0.014
            let sway2 = sin(px * 0.012 + t * 0.9) * 0.005
            let lift = cos(px * 0.012 + t * 1.1) * 0.008 + 0.013

            body.applyForce(CGVector(
                dx: (sway + sway2) * scale,
                dy: lift * scale
            ))
        }
    }

    private func cleanupOffscreenGlyphs() {
        for node in children where node.name == glyphNodeName {
            if node.position.y < -140 || node.position.x < -120 || node.position.x > size.width + 120 {
                node.removeFromParent()
            }
        }
    }

    private func trimGlyphOverflowIfNeeded() {
        let glyphNodes = children.filter { $0.name == glyphNodeName }
        guard glyphNodes.count > maxGlyphNodes else { return }

        let overflow = glyphNodes.count - maxGlyphNodes
        let toRemove = glyphNodes
            .sorted { $0.position.y < $1.position.y }
            .prefix(overflow)
        for node in toRemove {
            node.removeFromParent()
        }
    }
}

private struct LCG: RandomNumberGenerator {
    private var state: UInt64

    init(seed: UInt64) {
        state = seed == 0 ? 0x9E37_79B9_7F4A_7C15 : seed
    }

    mutating func next() -> UInt64 {
        state = state &* 6364136223846793005 &+ 1442695040888963407
        return state
    }
}
