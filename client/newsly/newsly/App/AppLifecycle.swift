import Foundation
import Observation
import os.log

private let appLifecycleLogger = Logger(
    subsystem: "com.newsly",
    category: "AppLifecycle"
)

/// Process lifecycle facts shared by app-owned features.
///
/// The app root is the only production writer. Feature models consume the
/// semantic phase and activation generation instead of observing UIKit or
/// SwiftUI lifecycle events independently.
@MainActor
@Observable
final class AppLifecycle {
    enum Phase: Equatable {
        case active
        case inactive
        case background
    }

    struct Activation: Equatable {
        enum Kind: Equatable {
            case initialLaunch
            case warmResume
        }

        let generation: UInt64
        let kind: Kind
        let occurredAt: Date
        let backgroundDuration: Duration?
    }

    private(set) var phase: Phase = .inactive
    private(set) var activation: Activation?
    private(set) var lastInterruptionReturnAt: Date?

    @ObservationIgnored
    private let now: () -> Date
    @ObservationIgnored
    private let processLaunchID = String(UUID().uuidString.prefix(8))
    @ObservationIgnored
    private var backgroundEnteredAt: Date?

    init(now: @escaping () -> Date = Date.init) {
        self.now = now
    }

    /// Records one phase fact from the app root.
    func record(_ newPhase: Phase) {
        guard newPhase != phase else { return }

        let previousPhase = phase
        let occurredAt = now()
        let previousActivationGeneration = activation?.generation

        switch newPhase {
        case .active:
            recordActivation(
                from: previousPhase,
                occurredAt: occurredAt
            )
        case .inactive:
            break
        case .background:
            if backgroundEnteredAt == nil {
                backgroundEnteredAt = occurredAt
            }
        }

        phase = newPhase
        logTransition(
            from: previousPhase,
            to: newPhase,
            previousActivationGeneration: previousActivationGeneration
        )
    }

    private func logTransition(
        from oldPhase: Phase,
        to newPhase: Phase,
        previousActivationGeneration: UInt64?
    ) {
        let generation = activation?.generation ?? 0
        let transitionKind: String
        if activation?.generation != previousActivationGeneration {
            transitionKind = activation?.kind.logValue ?? "none"
        } else if oldPhase == .inactive, newPhase == .active {
            transitionKind = "interruption_return"
        } else {
            transitionKind = "phase_only"
        }
        let backgroundMilliseconds = activation?.generation != previousActivationGeneration
            ? activation?.backgroundDuration?.milliseconds ?? 0
            : 0

        appLifecycleLogger.info(
            "Lifecycle transition | launch_id=\(self.processLaunchID, privacy: .public) pid=\(ProcessInfo.processInfo.processIdentifier, privacy: .public) old=\(oldPhase.logValue, privacy: .public) new=\(newPhase.logValue, privacy: .public) activation_generation=\(generation, privacy: .public) transition=\(transitionKind, privacy: .public) background_ms=\(backgroundMilliseconds, privacy: .public)"
        )
    }

    private func recordActivation(from previousPhase: Phase, occurredAt: Date) {
        if activation == nil {
            activation = Activation(
                generation: 1,
                kind: .initialLaunch,
                occurredAt: occurredAt,
                backgroundDuration: nil
            )
            backgroundEnteredAt = nil
            return
        }

        if let backgroundEnteredAt {
            let nextGeneration = (activation?.generation ?? 0) + 1
            activation = Activation(
                generation: nextGeneration,
                kind: .warmResume,
                occurredAt: occurredAt,
                backgroundDuration: .seconds(
                    max(0, occurredAt.timeIntervalSince(backgroundEnteredAt))
                )
            )
            self.backgroundEnteredAt = nil
            return
        }

        if previousPhase == .inactive {
            lastInterruptionReturnAt = occurredAt
        }
    }
}

private extension AppLifecycle.Phase {
    var logValue: String {
        switch self {
        case .active: "active"
        case .inactive: "inactive"
        case .background: "background"
        }
    }
}

private extension AppLifecycle.Activation.Kind {
    var logValue: String {
        switch self {
        case .initialLaunch: "initial_launch"
        case .warmResume: "warm_resume"
        }
    }
}

private extension Duration {
    var milliseconds: Int64 {
        let parts = components
        return (parts.seconds * 1_000) + (parts.attoseconds / 1_000_000_000_000_000)
    }
}
