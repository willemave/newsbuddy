//
//  OnboardingAudioStep.swift
//  newsly
//

import SwiftUI

struct OnboardingAudioStep: View {
    let viewModel: OnboardingViewModel
    @State private var typedInterests = ""
    @State private var isTyping = false

    var body: some View {
        VStack(spacing: 0) {
            onboardingHeaderBlock(
                eyebrow: "VOICE SETUP",
                title: "Tell us what you read",
                subtitle: "Topics, publications, podcasts, people you follow. Say it like you'd tell a friend.",
                titleAccessibilityIdentifier: "onboarding.audio.screen"
            )
            .padding(.top, 16)

            if isTyping {
                ScrollView {
                    TextEditor(text: $typedInterests)
                        .font(.modalBody)
                        .foregroundStyle(Color.onSurface)
                        .scrollContentBackground(.hidden)
                        .frame(minHeight: 160)
                        .accessibilityLabel("Your interests")
                        .accessibilityIdentifier("onboarding.typed_interests")
                    Button("Use these interests") {
                        Task { await viewModel.discoverTypedInterests(typedInterests) }
                    }
                    .disabled(typedInterests.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || viewModel.isLoading)
                    .accessibilityIdentifier("onboarding.typed_interests.submit")
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
            }

            Spacer()

            if viewModel.audioState == .transcribing {
                audioProcessingView
            } else if !isTyping {
                OnboardingMicButton(
                    audioState: viewModel.audioState,
                    durationSeconds: viewModel.audioDurationSeconds,
                    onStart: { Task { await viewModel.startAudioCapture() } },
                    onStop: { Task { await viewModel.stopAudioCaptureAndDiscover() } }
                )
            }

            Spacer()

            if viewModel.audioState != .transcribing {
                Button(isTyping ? "Use voice instead" : "Type instead") {
                    isTyping.toggle()
                    if isTyping { viewModel.resetAudioState() }
                }
                .font(.appCallout)
                .foregroundStyle(Color.onSurfaceSecondary)
                .accessibilityIdentifier("onboarding.type_instead")

                Button("Skip") {
                    viewModel.chooseDefaults()
                }
                .font(.appCallout.weight(.medium))
                .foregroundColor(.onSurfaceSecondary)
                .buttonStyle(OnboardingTextButtonStyle())
                .padding(.bottom, 8)
                .accessibilityIdentifier("onboarding.audio.skip")
            }

            if let error = viewModel.errorMessage {
                Text(error)
                    .font(.appCaption)
                    .foregroundColor(.statusDestructive)
                    .padding(.bottom, 8)
                    .accessibilityIdentifier("onboarding.audio.error")
            }
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .task {
            await viewModel.startAudioCaptureIfNeeded()
        }
    }

    private var audioProcessingView: some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.2)
                .tint(.onboardingText)
            Text("Processing your interests...")
                .font(.appCallout)
                .foregroundColor(.onSurfaceSecondary)
                .accessibilityIdentifier("onboarding.audio.state.transcribing")

            if hasTopicPreview {
                topicPreviewCard(
                    eyebrow: "WE HEARD",
                    title: viewModel.topicSummary ?? "Tuning your feed around your interests",
                    inferredTopics: viewModel.inferredTopics
                )
                .padding(.top, 8)
            }
        }
    }

    private var hasTopicPreview: Bool {
        (viewModel.topicSummary?.isEmpty == false) || !viewModel.inferredTopics.isEmpty
    }
}
