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

    private let dependencyFactory: RootDependencyFactory

    init() {
        KeychainManager.shared.configure(accessGroup: SharedContainer.keychainAccessGroup)
        AppChrome.configure()
        let apiClient = APIClient.shared
        let onboardingService = OnboardingService.shared
        let dependencyFactory = RootDependencyFactory(
            dependencies: RootDependencyFactory.Dependencies(
                apiClient: apiClient,
                authenticationService: AuthenticationService.shared,
                tokenStore: KeychainManager.shared,
                credentialSession: CredentialSession.shared,
                chatService: ChatService.shared,
                contentService: ContentService.shared,
                scraperConfigService: ScraperConfigService.shared,
                toastService: ToastService.shared,
                briefingService: LiveBriefingService(
                    apiClient: apiClient,
                    completeFirstRun: {
                        _ = try await onboardingService.markTutorialComplete()
                    }
                ),
                narrationPlaybackService: NarrationPlaybackService.shared,
                audioEpisodeService: AudioEpisodeService.shared,
                onboardingService: onboardingService,
                onboardingStateStore: OnboardingStateStore(
                    defaults: SharedContainer.userDefaults
                ),
                learningDeckService: LearningDeckService.shared,
                learningDeckStatusRegistry: LearningDeckStatusRegistry.shared,
                twitterShareService: TwitterShareService.shared,
                openAIService: OpenAIService.shared,
                appSettings: AppSettings.shared,
                xIntegrationService: XIntegrationService.shared,
                feedbackService: FeedbackService.shared,
                cliLinkService: CLILinkService(client: apiClient),
                localNotificationService: LocalNotificationService.shared,
                sharedDefaults: SharedContainer.userDefaults,
                makeVoiceDictationTranscriber: {
                    SpeechTranscriberFactory.makeVoiceDictationTranscriber()
                },
                makeChatNavigationCoordinator: { ChatNavigationCoordinator() }
            )
        )
        self.dependencyFactory = dependencyFactory
        let authenticationController = dependencyFactory.makeAuthenticationViewModel()
        self._runtime = State(
            initialValue: AppRuntime(
                dependencies: AppRuntime.Dependencies(
                    lifecycle: AppLifecycle(),
                    authenticationController: authenticationController,
                    makeAuthenticatedSession: dependencyFactory.makeAuthenticatedSession
                )
            )
        )
    }

    private var authViewModel: AuthenticationController {
        runtime.authenticationController
    }

    var body: some Scene {
        WindowGroup {
            Group {
                #if DEBUG
                if let visualState = E2ETestLaunch.visualState {
                    E2EVisualStateView(state: visualState)
                } else {
                    authenticatedPresentation
                }
                #else
                authenticatedPresentation
                #endif
            }
            .environment(runtime.lifecycle)
            .environment(dependencyFactory)
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

    @ViewBuilder
    private var authenticatedPresentation: some View {
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
            let settings = dependencyFactory.appSettings
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
                let response = try await dependencyFactory.cliLinkService.approve(
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
