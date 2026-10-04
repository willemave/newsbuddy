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
        let tokenStore = KeychainManager.shared
        let credentialSession = CredentialSession.makeAppSession(tokenStore: tokenStore)
        let apiClient = APIClient(
            session: .newslyDefault,
            credentialSession: credentialSession
        )
        let authenticationService = AuthenticationService(
            apiClient: apiClient,
            credentialSession: credentialSession
        )
        let onboardingService = OnboardingService(client: apiClient)
        let chatService = ChatService(client: apiClient)
        let contentService = ContentService(client: apiClient)
        let scraperConfigService = ScraperConfigService(client: apiClient)
        let audioEpisodeService = AudioEpisodeService(client: apiClient)
        let learningDeckService = LearningDeckService(client: apiClient)
        let learningDeckStatusRegistry = LearningDeckStatusRegistry(
            statusService: learningDeckService
        )
        let openAIService = OpenAIService(
            apiClient: apiClient,
            credentialSession: credentialSession
        )
        let voiceDictationService = VoiceDictationService(openAIService: openAIService)
        let speechTranscriber = SpeechTranscriberFactory.makeVoiceDictationTranscriber(
            voiceDictationService: voiceDictationService
        )
        let narrationPlaybackService = NarrationPlaybackService(
            nowPlayingController: .shared,
            markPlaybackFinished: { episodeID in
                try await audioEpisodeService.markPlaybackFinished(id: episodeID)
            }
        )
        let dependencyFactory = RootDependencyFactory(
            dependencies: RootDependencyFactory.Dependencies(
                apiClient: apiClient,
                authenticationService: authenticationService,
                tokenStore: tokenStore,
                credentialSession: credentialSession,
                chatService: chatService,
                contentService: contentService,
                scraperConfigService: scraperConfigService,
                toastService: ToastService.shared,
                briefingService: LiveBriefingService(
                    apiClient: apiClient,
                    completeFirstRun: {
                        _ = try await onboardingService.markTutorialComplete()
                    }
                ),
                narrationPlaybackService: narrationPlaybackService,
                audioEpisodeService: audioEpisodeService,
                onboardingService: onboardingService,
                onboardingStateStore: OnboardingStateStore(
                    defaults: SharedContainer.userDefaults
                ),
                learningDeckService: learningDeckService,
                learningDeckStatusRegistry: learningDeckStatusRegistry,
                twitterShareService: TwitterShareService.shared,
                openAIService: openAIService,
                appSettings: AppSettings.shared,
                xIntegrationService: XIntegrationService(apiClient: apiClient),
                feedbackService: FeedbackService(client: apiClient),
                cliLinkService: CLILinkService(client: apiClient),
                localNotificationService: LocalNotificationService.shared,
                sharedDefaults: SharedContainer.userDefaults,
                speechTranscriber: speechTranscriber
            )
        )
        self.dependencyFactory = dependencyFactory
        let authenticationController = dependencyFactory.makeAuthenticationController()
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
                    E2EVisualStateView(
                        state: visualState,
                        authViewModel: authViewModel
                    )
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
