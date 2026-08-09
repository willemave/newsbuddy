//
//  newslyApp.swift
//  newsly
//
//  Created by Willem Ave on 7/8/25.
//

import SwiftUI
import UIKit

@main
struct newslyApp: App {
    @State private var authViewModel = RootDependencyFactory.makeAuthenticationViewModel()
    @State private var cliLinkAlertMessage: String?

    private let cliLinkService = CLILinkService()

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
                        .environment(authViewModel)
                case .unauthenticated:
                    LandingView()
                        .environment(authViewModel)
                case .loading:
                    LoadingView()
                }
            }
            .onOpenURL { url in
                handleIncomingURL(url)
            }
            .alert("CLI Link", isPresented: cliLinkAlertIsPresented) {
                Button("OK", role: .cancel) { }
            } message: {
                Text(cliLinkAlertMessage ?? "")
            }
        }
    }

    private var cliLinkAlertIsPresented: Binding<Bool> {
        Binding(
            get: { cliLinkAlertMessage != nil },
            set: { isPresented in
                if !isPresented {
                    cliLinkAlertMessage = nil
                }
            }
        )
    }

    private func handleIncomingURL(_ url: URL) {
#if DEBUG
        if let debugLogin = DebugLoginLink(url: url) {
            let settings = AppSettings.shared
            settings.serverHost = debugLogin.serverHost
            settings.serverPort = debugLogin.serverPort
            settings.useHTTPS = debugLogin.useHTTPS
            if let appGroupID = SharedContainer.appGroupId,
               let sharedDefaults = UserDefaults(suiteName: appGroupID) {
                sharedDefaults.set(debugLogin.serverHost, forKey: ServerConfigurationDefaults.hostKey)
                sharedDefaults.set(debugLogin.serverPort, forKey: ServerConfigurationDefaults.portKey)
                sharedDefaults.set(debugLogin.useHTTPS, forKey: ServerConfigurationDefaults.useHTTPSKey)
            }
            authViewModel.startDebugSession(userID: debugLogin.userID)
            return
        }
#endif

        guard CLILinkScanPayload.canHandle(url) else {
            return
        }

        Task { @MainActor in
            guard case .authenticated = authViewModel.authState else {
                cliLinkAlertMessage = "Sign in to Newsbuddy before linking the CLI."
                return
            }

            do {
                let response = try await cliLinkService.approve(
                    scannedCode: url.absoluteString,
                    deviceName: UIDevice.current.name
                )
                cliLinkAlertMessage = "CLI linked with key prefix \(response.keyPrefix)."
            } catch {
                cliLinkAlertMessage = error.localizedDescription
            }
        }
    }
}
