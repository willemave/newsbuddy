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
    @Environment(\.scenePhase) private var scenePhase
    @State private var runtime: AppRuntime
    @State private var cliLinkAlertMessage: String?

    private let cliLinkService = CLILinkService()

    init() {
        KeychainManager.shared.configure(accessGroup: SharedContainer.keychainAccessGroup)
        AppChrome.configure()
        let authenticationController = RootDependencyFactory.makeAuthenticationViewModel()
        self._runtime = State(
            initialValue: AppRuntime(authenticationController: authenticationController)
        )
    }

    private var authViewModel: AuthenticationController {
        runtime.authenticationController
    }

    var body: some Scene {
        WindowGroup {
            Group {
                switch authViewModel.authState {
                case .authenticated(let user):
                    if let session = runtime.authenticatedSession,
                       session.user.id == user.id {
                        AuthenticatedRootView(session: session)
                            .environment(authViewModel)
                    } else {
                        LoadingView()
                    }
                case .unauthenticated:
                    LandingView()
                        .environment(authViewModel)
                case .loading:
                    LoadingView()
                }
            }
            .environment(runtime.lifecycle)
            .onChange(of: scenePhase, initial: true) { _, newPhase in
                runtime.record(AppLifecycle.Phase(newPhase))
            }
            .onChange(of: authViewModel.authState, initial: true) { _, authState in
                switch authState {
                case .authenticated(let user):
                    runtime.establishSession(for: user)
                case .loading, .unauthenticated:
                    runtime.clearAuthenticatedSession()
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

private extension AppLifecycle.Phase {
    init(_ scenePhase: ScenePhase) {
        switch scenePhase {
        case .active:
            self = .active
        case .inactive:
            self = .inactive
        case .background:
            self = .background
        @unknown default:
            self = .inactive
        }
    }
}
