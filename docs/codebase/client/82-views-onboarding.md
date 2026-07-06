# client/newsly/newsly/Views/Onboarding/

Source folder: `client/newsly/newsly/Views/Onboarding`

## Purpose
New-user onboarding flow UI including per-step onboarding screens, mic interaction, and tutorial/explanatory surfaces.

## Runtime behavior
- `OnboardingFlowView` owns the Observation-backed onboarding state with `@State` and routes each onboarding step to a dedicated view.
- Guides first-run users through profile capture, audio onboarding, suggestion selection, aggregator selection, Reddit setup, and tutorial transitions before the main tab UI appears.
- Uses custom mic interaction views to make the onboarding path more tactile than standard form sheets.
- Simple onboarding state transitions use shared `AppMotion` tokens; longer staggered reveals, the tutorial reveal, and breathing pulses remain local presentation effects with reduce-motion handling.

## Inventory scope
- Direct file inventory for `client/newsly/newsly/Views/Onboarding`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `client/newsly/newsly/Views/Onboarding/HowItWorksModal.swift` | `struct HowItWorksModal` | Types: `struct HowItWorksModal` |
| `client/newsly/newsly/Views/Onboarding/OnboardingAggregatorsStep.swift` | `struct OnboardingAggregatorsStep` | Aggregator source selection step. |
| `client/newsly/newsly/Views/Onboarding/OnboardingAudioStep.swift` | `struct OnboardingAudioStep` | Audio capture step. |
| `client/newsly/newsly/Views/Onboarding/OnboardingChoiceStep.swift` | `struct OnboardingChoiceStep` | Voice/text onboarding choice step. |
| `client/newsly/newsly/Views/Onboarding/OnboardingFlowView.swift` | `struct OnboardingFlowView` | Thin onboarding shell and step router. |
| `client/newsly/newsly/Views/Onboarding/OnboardingLoadingStep.swift` | `struct OnboardingLoadingStep` | Transition/loading step with progress states. |
| `client/newsly/newsly/Views/Onboarding/OnboardingMicButton.swift` | `struct OnboardingMicButton` | Types: `struct OnboardingMicButton` |
| `client/newsly/newsly/Views/Onboarding/OnboardingProgressHeader.swift` | `struct OnboardingProgressHeader` | Reusable onboarding step progress header. |
| `client/newsly/newsly/Views/Onboarding/OnboardingRedditStep.swift` | `struct OnboardingRedditStep` | Reddit source setup step. |
| `client/newsly/newsly/Views/Onboarding/OnboardingSharedComponents.swift` | shared onboarding helpers | Shared buttons, cards, surfaces, and selection controls. |
| `client/newsly/newsly/Views/Onboarding/OnboardingSuggestionsStep.swift` | `struct OnboardingSuggestionsStep` | Personalized suggestion selection step. |
