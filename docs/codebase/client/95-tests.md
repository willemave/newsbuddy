# client/newsly/newslyTests/ and newslyUITests/

Source folders: `client/newsly/newslyTests`, `client/newsly/newslyUITests`

## Purpose
iOS unit and UI tests for models, services, view models, routing, onboarding, chat, content detail, Knowledge, narration, tab coordination, and integrations.

## Unit test inventory
| File | Focus |
|---|---|
| `APIClientAuthTests.swift` | API auth/token behavior. |
| `AncientScrollRevealProgressTests.swift` | Onboarding reveal progress. |
| `AppSettingsTests.swift` | App settings persistence/defaults. |
| `CLILinkServiceTests.swift` | CLI QR link service behavior. |
| `ChatMessageDisplayTests.swift` | Chat message rendering/display models. |
| `ChatSessionViewModelTests.swift` | Chat session state. |
| `ChatTimelineReconcilerTests.swift` | Chat timeline reconciliation. |
| `ContentDetailTests.swift`, `ContentDetailViewModelTests.swift` | Content detail models and view model behavior. |
| `ContentDiscussionTests.swift`, `ContentSummaryTests.swift`, `ContentTimestampFormatterTests.swift` | Content discussion/summary/timestamp behavior. |
| `KnowledgeHubViewModelTests.swift` | Knowledge hub state. |
| `NarrationPlaybackSpeedOptionTests.swift` | Narration playback-rate options. |
| `OnboardingStateStoreTests.swift` | Onboarding state persistence. |
| `QuickMicViewModelTests.swift` | Quick Mic dictation state. |
| `ShareURLRoutingTests.swift` | Share extension/app URL routing. |
| `SubmissionStatusViewModelTests.swift` | Submission status state. |
| `TabCoordinatorViewModelTests.swift` | Tab coordination. |
| `UserProfileCodingTests.swift` | User profile encoding/decoding. |
| `XIntegrationServiceTests.swift` | X integration service behavior. |

## UI tests
| File | Focus |
|---|---|
| `newslyUITests/newslyUITests.swift` | UI smoke test target. |

## Integration points
- Xcode target membership is managed in `newsly.xcodeproj`.
- Backend contract changes may require both model/service tests and generated contract updates.
