//
//  newslyApp.swift
//  newsly
//
//  Created by Willem Ave on 7/8/25.
//

import SwiftUI

@main
struct newslyApp: App {
    @StateObject private var authViewModel = AuthenticationViewModel()

    init() {
        AppChrome.configure()
        if let accessGroup = SharedContainer.keychainAccessGroup {
            KeychainManager.shared.configure(accessGroup: accessGroup)
        }
    }

    var body: some Scene {
        WindowGroup {
            Group {
                switch authViewModel.authState {
                case .authenticated(let user):
                    AuthenticatedRootView(user: user)
                        .environmentObject(authViewModel)
                case .unauthenticated:
                    LandingView()
                        .environmentObject(authViewModel)
                case .loading:
                    LoadingView()
                }
            }
        }
    }
}
