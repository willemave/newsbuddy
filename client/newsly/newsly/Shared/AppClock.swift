//
//  AppClock.swift
//  newsly
//

import Foundation

enum AppClock {
    static var now: Date {
        E2ETestLaunch.visualNow ?? Date()
    }
}
