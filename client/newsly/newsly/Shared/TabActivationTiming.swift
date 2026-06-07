//
//  TabActivationTiming.swift
//  newsly
//

import Foundation

enum TabActivationTiming {
    static let settleDelay: Duration = .milliseconds(350)

    static func waitForSettle() async -> Bool {
        do {
            try await Task.sleep(for: settleDelay)
            return !Task.isCancelled
        } catch {
            return false
        }
    }
}
