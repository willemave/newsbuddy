# iOS Chat Stability Refactor

**Status:** Core stability implementation complete; strict Phase 6 view decomposition, role-specific bubble split, Observation migration, preview expansion, signpost instrumentation, and per-voice council retry complete; manual validation follow-up remains
**Opened:** 2026-04-15
**Owner:** iOS
**Target:** `client/newsly/newsly/Views/ChatSessionView.swift`, `ChatSessionViewModel`, `VoiceDictationService`, `ActiveChatSessionManager`
**Deployment target:** iOS 18.5+ (full iOS 18 API set available, including `ScrollPosition`)

## Implementation progress

Updated 2026-04-16:

- [x] Stable timeline identity model: `ChatTimelineID` and `ChatTimelineItem`.
- [x] Pure `ChatTimelineReconciler` with focused XCTest coverage.
- [x] `ChatSessionViewModel` now uses one `timeline` source of truth; the old `transcriptMessages` / `activeTurnMessages` partition is removed.
- [x] `ChatSessionView` renders one `ForEach(viewModel.timeline)` keyed by stable timeline ids.
- [x] Chat scroll no longer uses `ScrollViewReader`, persisted `ChatScrollStateStore`, string scroll ids, or competing `onChange` handlers.
- [x] `ChatScrollStateStore.swift` deleted.
- [x] `ActiveChatSessionManager` resets on logout and fixes `hasActiveSession(forContentId:)`.
- [x] Chat view stops background tracking when opened and resets dictation state on disappear.
- [x] `VoiceDictationService` deactivates `AVAudioSession`, clears feature callbacks on reset, observes interruptions / route changes, and applies a transcription deadline.
- [x] Council candidate cards render `processing` and `failed` states.
- [x] Failed local send rows render an inline retry affordance.
- [x] `ChatSessionView` now has a single route-based initializer.
- [x] `ChatSessionRoute` carries optional session summary data plus a `stableKey`.
- [x] App navigation now keys `ChatSessionView` by `route.stableKey`, and the dead nested chat destination inside `ChatSessionView` has been removed so external entry stays coordinator-owned.
- [x] View-triggered send/council/retry/dig-deeper/voice actions now run through VM-owned task handles, and cancellation on disappear no longer converts into a failed-send UI state.
- [x] `handleDisappear()` now hands content-backed in-flight processing back to `ActiveChatSessionManager` before cancelling view-owned work, so background completion notifications and badges survive navigation away.
- [x] `ActiveChatSessionManager.startTracking` is now idempotent per session/message pair and cancels any replaced polling task before starting a new one, avoiding duplicate background pollers during repeated handoff.
- [x] `ChatDependencies` explicitly injects chat, dictation, and active-session services into the chat view / view model.
- [x] Terminal token-refresh failures now post `.authDidLogOut` so chat background polling is reset.
- [x] Council branch selection is view-model owned, single-flight, and cancels the previous selection task before starting a new one.
- [x] Council branch selection now exposes a 10s timeout banner with cancel action.
- [x] Thinking indicator is rendered as a bottom overlay instead of a timeline row once messages exist.
- [x] `VoiceDictationService` now uses an `AudioRecordingSessionLease` helper and emits start/stop haptics.
- [x] `30-verification.md` records the manual verification matrix, automated coverage, and blocked items.
- [x] View decomposition extracted `ChatMessageList`, `ChatComposerDock`, `MessageBubble`, `CouncilBranchTabs`, `CouncilCandidatesBubble`, `ChatActivityViews`, `ChatErrorBanner`, `ChatEmptyState`, `ArticlePreviewCard`, `ChatSecondaryPanel`, and `AssistantFeedOptionsSection` under `Views/Chat/`.
- [x] Strict Phase 6 extraction moved toolbar content, share sheet, selectable text, dig-deeper text view, root previews, and `MessageRow` dispatch out of `ChatSessionView.swift`; the root file is now 277 lines with named action callbacks.
- [x] `ChatMessage.formattedTime` now uses cached timestamp parsers/display formatter instead of constructing formatter objects from hot bubble render paths.
- [x] Role-specific bubble split extracted `AssistantMessageBubble` and `UserMessageBubble`, leaving `MessageBubble` as a small dispatcher plus process-summary row.
- [x] Observation migration converted `ChatSessionViewModel` to `@Observable`, removed `@Published`, stores it as `@State` in `ChatSessionView`, and ignores implementation-only timers/tasks/pending maps.
- [x] Post-edit cleanup removed stale scroll identity, tightened reset callback cleanup, and hardened council selection task cleanup.
- [x] Preview expansion added reusable `ChatPreviewFixtures` and `#Preview` coverage for message rows, assistant feed options, council tabs/candidates, article/context panels, empty/error states, composer, and the message list.
- [x] Phase 0 signpost instrumentation added under subsystem `com.newsly.chat`, category `perf`.
- [x] Baseline trace capture checklist added under `baseline/baseline.md`.
- [x] Backend support for per-voice council retry added via `POST /api/content/chat/sessions/{session_id}/council/retry`; council start now preserves partial branch failures as failed candidates.
- [x] iOS failed council candidate cards now expose a per-voice retry action wired through `ChatSessionViewModel.retryCouncilCandidate`.
- [x] Long-transcript scroll matrix now has a Maestro E2E flow covering older-message scrollback, send while scrolled up, "Jump to latest", and backend persistence of the follow-up turn.
- [x] Backend `display_key` contract added to `ChatMessageDto`; iOS timeline identity now prefers that explicit row key and keeps the derived identity as a compatibility fallback.
- [x] Feed option action state is now owned above row views in both chat and Quick Mic; per-bubble `AssistantFeedOptionActionModel` ownership is removed.
- [x] Phase 12 streaming-readiness seam documented in `ChatSessionViewModel`; streaming remains deferred.
- [x] Validation: simulator build passed; focused chat/auth/mic slice passed 27/27 after per-voice retry, again after VM-owned action-task cancellation, again after `MessageRow` extraction, and 28/28 after background polling handoff on disappear; focused timeline identity slice passed 7/7 after `display_key`; focused feed-option ownership slice passed 17/17; focused strict Phase 6 extraction/timestamp slice passed 17/17; full `newslyTests` passed 100/100 after per-voice retry; backend chat router/model tests passed 39/39.
- [ ] Still pending from the broader plan: baseline Instruments trace capture, local/device execution of the scroll matrix, and manual device audio verification.

---

## 1. Why this initiative

Chat is the most interaction-dense surface in the app and the one users experience as "glitchy". The symptoms are not random; each has an identifiable cause in code. Left alone, these compound: each new feature (council, dictation, share-sheet handoff, provider switching, split-view context) adds an independent state machine on top of an already-competing set of scroll, session, and reconciliation flows.

This document diagnoses the root causes, lays out a target architecture using standard iOS 18 patterns, and sequences the refactor into independently shippable PRs.

---

## 2. Observed symptoms → root cause

| Symptom | Root cause | Evidence |
| --- | --- | --- |
| A user bubble appears, then "jumps" or briefly duplicates when the assistant responds | Messages are partitioned into two `@Published` arrays (`transcriptMessages` + `activeTurnMessages`) and rendered in **two separate `ForEach` blocks**. Server reconciliation reclassifies the same row from one array into the other, which SwiftUI sees as a structural tree change. | `ChatSessionViewModel.swift:17-18, 142-143, 325-349`; `ChatSessionView.swift:590, 595` |
| Scroll "drifts up" or "snaps" when the assistant finishes answering | The thinking indicator is rendered as a sibling row (`id: "__thinking__\|sessionId"`), and `.scrollPosition(id:)` is anchored to it while `isSending == true`. When `isSending` flips false, the anchored row disappears, leaving `scrolledMessageId` pointing at a stale id. | `ChatSessionView.swift:312-314, 600-608, 614, 733-755` |
| Scroll feels "fought" or resets unexpectedly after sending | Three independent scroll mechanisms run at the same time: `.scrollPosition(id:)` (iOS 17+ binding), a `ScrollViewReader.scrollTo` (imperative), and `ChatScrollStateStore` (UserDefaults persistence), plus four `onChange` handlers (`scrolledMessageId`, `allMessages.count`, `isSending`, `isLoading`) that each adjust scroll. The binding change from `scrollTo` triggers `updateIsAtBottom`, which flips `followLatest`, which changes what the next `onChange(count)` does — a feedback loop per message. | `ChatSessionView.swift:513-648, Shared/ChatScrollStateStore.swift` |
| After a restore failure (e.g., anchor message not in transcript), scroll never recovers for the rest of the session | `hasRestoredScroll` latches `true` after the first call to `restoreScrollPositionIfNeeded`, silently disabling future restores. | `ChatSessionView.swift:757-781` |
| Opening a session with a pending message from the Share Extension can show stale scroll position | `resetsScrollStateOnOpen` is only `true` for `pendingCouncilPrompt` routes, so regular pending-message routes still restore the old offset. | `ChatSessionView.swift:297-298, 638-644` |
| New-session send can briefly show a duplicate user bubble | The route init seeds a placeholder with `id: route.pendingMessageId ?? route.sessionId`. On send, `activeTurnMessages = [response.userMessage]` replaces it with the server echo — but if the seeded placeholder and the server's echo have different ids/timestamps, the ForEach row is replaced rather than reconciled. | `ChatSessionView.swift:273-286`; `ChatSessionViewModel.swift:233-263` |
| `await viewModel.sendMessage()` returns immediately, so subsequent awaits don't actually wait | `sendMessage` wraps the real work in `Task { … }` detached from the caller (VM:244). The outer `await` is misleading. If the view dismisses, the detached task keeps writing to an observed VM. | `ChatSessionViewModel.swift:244-263, 219-231` |
| Polling continues after logout | `ActiveChatSessionManager.shared` is a singleton whose `activeSessions`, `completedSessions`, and `pollingTasks` are never reset on logout. Polling continues against an invalidated token. | `ActiveChatSessionManager.swift:52, no deinit` |
| Voice dictation suppresses Live-tab TTS and other apps' audio even after stopping | `AVAudioSession.setActive(true)` is called on start, but `setActive(false, options: .notifyOthersOnDeactivation)` is **never** called. The session stays live. | `VoiceDictationService.swift:133-139` |
| A phone call during dictation silently corrupts the recording | `AVAudioSession.interruptionNotification` and `routeChangeNotification` are not observed. | `VoiceDictationService.swift` (no observers) |
| Transcription hangs indefinitely if the backend is unreachable | No URLSession timeout on the transcription request and no client-side deadline. | `VoiceDictationService.swift:350-367`, `OpenAIService.transcribeAudio` |
| Council branch tab shows loading forever on a network hang | `selectCouncilBranch` has no cancel/timeout; `selectingCouncilChildSessionId` is only cleared in `defer`, which runs only when the `await` completes. | `ChatSessionViewModel.swift:395-413` |
| A failed expert voice in a council response renders as an empty card with no error | `CouncilCandidate.status` exists but is not used in the rendering path. | `ChatMessage.swift:99-117`; `ChatSessionView.swift:1243-1340` |

These are the *named* glitches. The underlying issue is structural: state and side-effects live in too many places, and the view has four parallel coordination systems (message partition, scroll coordination, focus coordination, voice coordination) that were each reasonable in isolation but interact badly.

---

## 3. Verified inventory of the chat surface

| File | Size | Role |
| --- | --- | --- |
| `Views/ChatSessionView.swift` | 1786 lines before implementation; 277 lines after strict Phase 6 extraction | Root chat shell, route ownership, service actions, and `@State`-owned observable VM after implementation. Share sheet, toolbar, selectable UIKit text wrappers, dig-deeper text view, previews, and row dispatch are extracted. |
| `ViewModels/ChatSessionViewModel.swift` | 591 lines before implementation; 738 lines after timeline/council/voice stabilization | `@MainActor @Observable` with one timeline source of truth, VM-owned action task handles, council flow, and voice dictation callbacks. |
| `Views/ChatSessionHistoryView.swift` | 136 lines | Sessions list. Computed `filteredSessions` used for deletion — stable-enough but should key on `session.id`. |
| `Shared/ChatScrollStateStore.swift` | 58 lines | UserDefaults-backed scroll-offset persistence. **Slated for deletion.** |
| `Services/ActiveChatSessionManager.swift` | 218 lines | Singleton, background polling for sessions the user isn't currently viewing, local notifications on completion, view-disappear handoff for content-backed in-flight chats, and idempotent per-session tracking. |
| `Services/ChatService.swift` | 331 lines | Session CRUD, message send/poll, council start/select, initial suggestions. |
| `Services/VoiceDictationService.swift` | 388 lines | Singleton. AVAudioRecorder → OpenAI transcription. No session deactivation, no interruption handling, no timeout. |
| `Services/SpeechTranscribing.swift` | 114 lines | `SpeechTranscribing` protocol + factory. |
| `Services/ChatNavigationCoordinator.swift` | 32 lines | Global `@Published pendingRoute` already used by `ContentView`, notifications, content detail, and short-form quick actions. **Document as the exclusive external-entry coordinator and remove bypasses.** |
| `Models/ChatMessage.swift` | 263 lines | `ChatMessage` (Codable + Identifiable, **not Equatable**), `CouncilCandidate`, `AssistantFeedOption`, `scrollIdentity` computed String. |

---

## 4. Principles the new design enforces

1. **One source of truth per concept.** One timeline array. One scroll-follow signal. One `ChatSessionView` initializer. One app-owned microphone service with per-feature callback ownership.
2. **Stable timeline identity.** `ChatMessage.id` is not a durable UI identity in this app: session detail uses per-response display ids while message-status polling uses backing async message ids. Rows key on a separate `ChatTimelineID`, not on `ChatMessage.id`.
3. **iOS-native scroll with explicit bottom tracking.** Use iOS 18's `ScrollPosition`, `.defaultScrollAnchor(.bottom)`, `.scrollTargetLayout()`, and `onScrollGeometryChange`. No `ScrollViewReader`, no UserDefaults offset persistence, no several competing `onChange` handlers.
4. **Observation over ObservableObject.** `@Observable` view model held as `@State` in the owning view. Narrower invalidation, no `objectWillChange` fan-out on unrelated property changes.
5. **Structured concurrency.** Every long-lived async operation is owned by the view's `.task(id:)` or by an explicit `Task?` stored on the VM and cancelled on `sessionId` change. No detached `Task { … }` inside a VM method that returns before its work completes.
6. **Explicit polling ownership.** If the chat view owns polling, dismissal cancels it. If polling must survive dismissal, the view hands it off to `ActiveChatSessionManager` with the message id and display metadata before disappearing.
7. **Scoped audio sessions.** Any code that activates `AVAudioSession` deactivates it in a `defer` on every path, including cancellation and interruption.
8. **Body reads as layout.** No business logic in closures. Actions are named private methods. Business logic lives in services and the VM.
9. **Stable view tree.** No root-level `if/else` branches that swap the entire subtree. Loading/empty/error render as overlays or inline rows within a stable container.

---

## 5. Target architecture

### 5.1 Stable timeline state

```swift
enum ChatTimelineID: Hashable, Sendable {
    case server(sourceMessageId: Int, role: ChatMessageRole, displayType: ChatMessageDisplayType)
    case local(UUID)

    var sortKey: String { ... }
}

struct ChatTimelineItem: Identifiable, Equatable {
    let id: ChatTimelineID
    var message: ChatMessage
    var pendingMessageId: Int?
    var retryText: String?
}

@MainActor
@Observable
final class ChatSessionViewModel {
    // Public observable state
    private(set) var session: ChatSessionSummary?
    private(set) var timeline: [ChatTimelineItem] = []
    private(set) var loadState: LoadState = .idle
    private(set) var sendState: SendState = .idle
    var inputText: String = ""
    var errorBanner: ErrorBanner?

    // Council
    private(set) var selectingCouncilChildSessionId: Int?

    // Voice
    private(set) var voice: VoiceState = .disabled

    // Lifecycle
    @ObservationIgnored
    private var loadTask: Task<Void, Never>?
    @ObservationIgnored
    private var sendTask: Task<Void, Never>?
    @ObservationIgnored
    private var pollTask: Task<Void, Never>?
    @ObservationIgnored
    private var selectCouncilTask: Task<Void, Never>?
    @ObservationIgnored
    private var pendingSends: [UUID: PendingSend] = [:]
    @ObservationIgnored
    private var localIdentityAliases: [ChatTimelineID: UUID] = [:]

    enum LoadState { case idle, loading, loaded, failed(String) }
    enum SendState { case idle, sending(localId: UUID), awaitingAssistant(messageId: Int) }
    enum VoiceState { case disabled, idle, recording, transcribing, failed(String) }

    init(route: ChatSessionRoute, dependencies: ChatDependencies = .live) { ... }
}
```

- A single `timeline` array replaces `transcriptMessages` + `activeTurnMessages` + `initialPendingUserMessage`.
- "Processing" is expressed by the **last message's `status`**, not by which array it lives in.
- The thinking indicator becomes a render-time decision based on `sendState`, not a separate list row.
- `ChatMessage` gains `: Equatable` so reconciliation can avoid redundant assignment and tests can assert value transitions. It is not relied on as a SwiftUI performance fix.
- `ChatTimelineID.server` is derived from `sourceMessageId ?? id`, role, and display type. This survives the current mismatch between session-detail display ids and message-status display ids.
- Backend follow-up complete: `ChatMessageDto.display_key` exposes the stable timeline key, and the client only derives a key as a compatibility fallback.

### 5.2 Pure reconciliation algorithm

```swift
struct PendingSend: Equatable {
    let localId: UUID
    let text: String
    var messageId: Int?
    let createdAt: String
}

struct ChatTimelineReconciler {
    func reconcile(
        current: [ChatTimelineItem],
        detail: ChatSessionDetail,
        pendingSends: [UUID: PendingSend],
        localIdentityAliases: inout [ChatTimelineID: UUID]
    ) -> [ChatTimelineItem] {
        let incoming = detail.messages.filter { !$0.content.isEmpty || $0.hasCouncilCandidates }
        var byId = Dictionary(uniqueKeysWithValues: current.map { ($0.id, $0) })

        for message in incoming {
            let serverId = ChatTimelineID.server(
                sourceMessageId: message.sourceMessageId ?? message.id,
                role: message.role,
                displayType: message.displayType
            )

            var aliasedLocalId = localIdentityAliases[serverId]
            if message.isUser, let pending = pendingSends.values.first(where: { pending in
                pending.messageId == message.sourceMessageId || pending.messageId == message.id
            }) {
                localIdentityAliases[serverId] = pending.localId
                aliasedLocalId = pending.localId
            }

            let rowId = aliasedLocalId.map(ChatTimelineID.local) ?? serverId
            let matchingPending = aliasedLocalId.flatMap { localId in
                pendingSends.values.first { $0.localId == localId }
            }
            byId[rowId] = ChatTimelineItem(
                id: rowId,
                message: message,
                pendingMessageId: matchingPending?.messageId
            )
        }

        for pending in pendingSends.values where pending.messageId == nil {
            let localId = ChatTimelineID.local(pending.localId)
            byId[localId] = byId[localId] ?? ChatTimelineItem(
                id: localId,
                message: ChatMessage.placeholder(content: pending.text, timestamp: pending.createdAt),
                pendingMessageId: nil,
                retryText: pending.text
            )
        }

        // Local aliases are kept for the lifetime of the view model so a row that started
        // as a local pending send does not later flip to the server id during detail refresh.
        return byId.values
            .filter { item in
                guard case .local(let uuid) = item.id else { return true }
                return pendingSends[uuid] != nil
                    || localIdentityAliases.values.contains(uuid)
                    || item.retryText != nil
            }
            .sorted { lhs, rhs in
                (lhs.message.timestamp, lhs.id.sortKey) < (rhs.message.timestamp, rhs.id.sortKey)
            }
    }
}
```

**Properties of this reconciliation:**
- Rows never move between arrays; there is only one list.
- `ForEach(viewModel.timeline)` keys on `ChatTimelineItem.id`, not `ChatMessage.id`.
- A pending user row keeps its local identity after `sendMessageAsync` returns. The row's message payload updates in place with the server echo.
- `localIdentityAliases` keeps that local identity for the lifetime of the VM, so a later detail refresh does not flip the row to a server-derived id.
- The assistant reply uses a stable server-derived identity, so the status endpoint and later detail refresh agree on the same row even if their integer `id` fields differ.
- The reconciler is a pure type with XCTest coverage. The VM calls it; it does not own the algorithm.

### 5.3 Single scroll strategy

```swift
struct ChatMessageList: View {
    let items: [ChatTimelineItem]
    let isAssistantThinking: Bool
    @State private var scrollPosition = ScrollPosition(edge: .bottom)
    @State private var isNearBottom = true

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                ForEach(items) { item in
                    MessageRow(item: item)
                        .id(item.id)
                }
            }
            .scrollTargetLayout()
            .padding(.horizontal)
            .padding(.top)
        }
        .scrollPosition($scrollPosition)
        .defaultScrollAnchor(.bottom)
        .onScrollGeometryChange(for: Bool.self) { geometry in
            let distanceFromBottom =
                geometry.contentSize.height
                - geometry.visibleRect.maxY
                + geometry.contentInsets.bottom
            return distanceFromBottom < 48
        } action: { _, newValue in
            isNearBottom = newValue
        }
        .onChange(of: items.last?.id) { _, _ in
            guard isNearBottom else { return }
            scrollPosition.scrollTo(edge: .bottom)
        }
        .overlay(alignment: .bottom) {
            if isAssistantThinking { ChatThinkingOverlay().padding(.bottom, 8) }
        }
        .scrollDismissesKeyboard(.interactively)
        .contentMargins(.bottom, isAssistantThinking ? 72 : 12, for: .scrollContent)
    }
}
```

- `.defaultScrollAnchor(.bottom)` pins the initial position.
- `isNearBottom` is the only follow-latest signal. It replaces `isAtBottom`, `followLatest`, `scrolledMessageId`, and persisted scroll state.
- Thinking indicator is an overlay with bottom content margin, not a list row. It does not perturb row identity when `isAssistantThinking` flips and does not cover the last message.
- Gone: `ScrollViewReader`, `proxy.scrollTo`, `scrolledMessageId`, `hasRestoredScroll`, `isAtBottom`, `followLatest`, `ChatScrollStateStore`, the four `onChange` handlers, and the custom restore logic.
- "Jump to latest" chip is driven by `!isNearBottom` and new content, and calls `scrollPosition.scrollTo(edge: .bottom)`.

### 5.4 Single view initializer

```swift
struct ChatSessionView: View {
    @State private var viewModel: ChatSessionViewModel
    @FocusState private var isInputFocused: Bool
    @State private var shareContent: ShareContent?
    @State private var isContextPanelPresented = false
    @State private var isCouncilSettingsPresented = false

    init(
        route: ChatSessionRoute,
        dependencies: ChatDependencies = .live,
        onShowHistory: (() -> Void)? = nil
    ) {
        _viewModel = State(initialValue: ChatSessionViewModel(route: route, dependencies: dependencies))
        self.onShowHistory = onShowHistory
    }

    var body: some View {
        ChatShell(viewModel: viewModel, /* … */)
            .task(id: route.stableKey) { await viewModel.load() }
            .onDisappear { viewModel.handleDisappear() }
    }
}
```

- The three existing initializers (`session:`, `route:`, `sessionId:`) collapse to one. Callers construct a `ChatSessionRoute` value (the existing type; extended to hold a `session` summary for the "came from history" case).
- `@Observable` VM held as `@State`. No `@StateObject`.
- Dependencies are explicit initializer inputs for the root view and VM. Environment injection can still be used higher up, but the VM cannot depend on an environment value from `init`.
- `route.stableKey` includes `sessionId`, `pendingMessageId`, `pendingCouncilPrompt`, and any initial user-message seed, so navigation does not accidentally reuse a stale VM for a materially different route.
- `handleDisappear()` cancels voice immediately. For message polling, it either cancels view-owned work or hands polling to `ActiveChatSessionManager` depending on the selected lifecycle policy in §5.6.

### 5.5 Voice dictation lifecycle

```swift
final class AudioRecordingSessionLease {
    func begin() throws {
        try AVAudioSession.sharedInstance().setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
        try AVAudioSession.sharedInstance().setActive(true)
    }
    func end() {
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}

@MainActor
@Observable
final class VoiceDictationService: NSObject, SpeechTranscribing, AVAudioRecorderDelegate {
    static let shared = VoiceDictationService()

    @ObservationIgnored
    private let audioSession = AudioRecordingSessionLease()
    @ObservationIgnored
    private var interruptionObserver: NSObjectProtocol?
    @ObservationIgnored
    private var routeChangeObserver: NSObjectProtocol?

    func start() async throws {
        try audioSession.begin()
        do {
            observeAudioNotifications()
            // ... create recorder and start recording
        } catch {
            cleanup()
            throw error
        }
    }

    func stop() async throws -> String {
        let fileURL = try stopRecorderAndReturnFile()
        audioSession.end() // release mic before network transcription starts
        defer { cleanupAfterTranscription() }
        return try await transcribe(fileURL, deadline: .seconds(60))
    }

    func cancel() { cleanup() }
    func reset() { cleanup() /* also clears callbacks */ }

    private func cleanup() {
        audioSession.end()
        interruptionObserver.map(NotificationCenter.default.removeObserver)
        interruptionObserver = nil
        routeChangeObserver.map(NotificationCenter.default.removeObserver)
        routeChangeObserver = nil
        // cancel autoStopTask, invalidate meteringTimer, stop recorder, remove file, clear callbacks
    }

    private func observeAudioNotifications() {
        interruptionObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification, object: nil, queue: .main
        ) { [weak self] note in
            guard let self, let type = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                  AVAudioSession.InterruptionType(rawValue: type) == .began else { return }
            Task { @MainActor in self.cancelWithUserMessage("Recording paused (interruption)") }
        }
        routeChangeObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.cancelWithUserMessage("Recording stopped because the audio route changed") }
        }
    }
}
```

- `VoiceDictationService` remains app-owned because Quick Mic, onboarding, discovery personalization, tweet suggestions, and chat all use the same microphone path through `SpeechTranscriberFactory`.
- The singleton is no longer treated as feature state. Each feature owns its callbacks and calls `reset()` on disappear to release callbacks, recorder state, and the audio session.
- The audio session is deactivated immediately after recording stops, before upload/transcription begins.
- `OpenAIService.transcribeAudio` uses either a custom `URLSessionConfiguration` or an explicit task-group deadline. Errors surface as `VoiceDictationError.transcriptionTimedOut`.

### 5.6 Session lifecycle & navigation

- **`ActiveChatSessionManager`** subscribes to `NotificationCenter.default` for `.authDidLogOut` (new notification posted by `AuthenticationService` on explicit logout and on refresh failure). On receipt it cancels all `pollingTasks` and clears `activeSessions` and `completedSessions`.
- `ActiveChatSessionManager.hasActiveSession(forContentId:)` is fixed to use `sessionIdsByContentId`, not `activeSessions[contentId]`, because `activeSessions` is keyed by session id.
- The view calls `ActiveChatSessionManager.shared.stopTracking(sessionId:)` on `.task(id:)` start so that a session the user opens stops being polled in the background.
- If a view disappears while a message is still processing and the product expects a completion notification, `handleDisappear()` calls `startTracking(session:contentId:contentTitle:messageId:)` before cancelling the view poll. If the product chooses view-owned polling only, Flow I is updated accordingly.
- **`ChatNavigationCoordinator`** is already used by `ContentView`, local notifications, content detail, and short-form quick actions. This phase makes it the documented exclusive external-entry coordinator and removes any remaining direct nested chat navigation.
- The in-view `navigationDestination(item: $navigateToNewSessionId)` at `ChatSessionView.swift:456-458` that opens a nested `ChatSessionView` is re-evaluated: if the intent is "user tapped a cross-reference", it should still work, but the stacked-chat UX is checked in the test matrix.

### 5.7 Council hardening

- `CouncilCandidateCard` reads `candidate.status` and renders three states: `success` (current), `processing` (spinner + "thinking"), `failed` (inline error chip with a `Retry this voice` affordance that re-triggers just that persona).
- `selectCouncilBranch` stores its task on the VM: `selectCouncilTask?.cancel()` before starting a new one. On error, the **previous** `activeChildSessionId` is preserved in local UI state; only a successful response commits the change.
- `selectingCouncilChildSessionId` gains a deadline: after 10 seconds with no response, it surfaces a `"Switching branch… tap to cancel"` affordance.
- `canStartCouncil` is a computed property on `ChatSessionSummary` (not on the VM) so it is trivially testable and shared with Settings.

---

## 6. Phased execution plan

Each phase is **independently shippable** and **independently revertible** by reverting its single PR. Phases are ordered so earlier phases reduce surface area for later phases.

### Phase 0 — Instrumentation baseline *(~0.5 day, 1 PR)*
**Goal:** Quantify current behavior so we can recognize improvement and catch regressions.

- Add `os_signpost` intervals in `ChatSessionViewModel`:
  - `load-session`, `send-message`, `poll-cycle`, `reconcile-detail`, `start-council`, `select-council-branch`, `apply-voice-transcript`.
- Subsystem `com.newsly.chat`, category `perf`.
- Record an Instruments profile of:
  1. cold open of a 50-message session,
  2. send + poll of a 3-second assistant reply,
  3. start + receive a 3-voice council response,
  4. record 10 seconds of dictation and stop.
- Save the `.trace` files in `docs/initiatives/ios-chat-stability-2026-04/baseline/` (or an internal location) with a short `baseline.md` noting observations.
- Add `#Preview` entries covering: empty, loading, single-turn, council 2-voice, council 3-voice, council with one failed voice, 50-message scroll, processing-in-flight, error banner. Those previews survive the refactor and become regression canaries.

**Definition of done:** Baseline trace + preview set committed. No production behavior change.
**Rollback:** Revert PR. Zero risk.

### Phase 1 — Timeline identity contract *(~0.5 day, 1 PR)*
**Goal:** Establish stable row identity before changing rendering.

- Add `ChatTimelineID` and `ChatTimelineItem`.
- Add `ChatTimelineID.server(sourceMessageId:role:displayType:)` derivation from existing API fields.
- Add `ChatMessage: Equatable`.
- Leave the existing dual arrays and rendering untouched for this phase.
- Backend companion complete: `display_key` is emitted by `ChatMessageDto` so status-endpoint and session-detail rows can share explicit timeline identity.

**Definition of done:** XCTest cases prove that status-endpoint rows and session-detail rows for the same backing message resolve to the same `ChatTimelineID`. No production UI behavior change.
**Risk:** Low. Mostly additive.
**Rollback:** Revert PR.

### Phase 2 — Unified timeline model *(~1 day, 1 PR)*
**Goal:** Eliminate the "rows jump" class of bugs.

- Replace `transcriptMessages` + `activeTurnMessages` + `initialPendingUserMessage` with a single `timeline: [ChatTimelineItem]`.
- Implement `ChatTimelineReconciler` from §5.2 as a pure helper.
- Phase sequencing note: this phase originally kept the VM as `ObservableObject`; Phase 4 now migrates it to `@Observable`.
- Thinking indicator remains a sibling row for this PR to keep the diff minimal; move to overlay in Phase 5.
- Update `ChatSessionView` to render one `ForEach(viewModel.timeline)` keyed by `ChatTimelineItem.id`.
- Remove `scrollIdentity` from `ChatMessage.swift` only after all call sites use timeline ids.

**Definition of done:** `newslyTests` includes reconciliation cases for local pending row -> server user row, status assistant row -> detail assistant row, duplicate suppression mid-poll, process-summary ordering, council candidate ordering, failed-send retry row, and rapid sends.
**Risk:** Medium. Reconciliation is the riskiest logic change.
**Rollback:** Revert PR. Previous dual-array code returns.

### Phase 3 — Single initializer, dependencies, and task ownership *(~0.75 day, 1 PR)*
**Goal:** Remove duplicated route seeding and detached async work without changing observation mode.

- Phase sequencing note: this phase originally kept `ChatSessionViewModel` as `ObservableObject`; Phase 4 now migrates it to `@Observable`.
- Collapse the three `ChatSessionView` initializers into `init(route:dependencies:onShowHistory:)`.
- Extend `ChatSessionRoute` with `session: ChatSessionSummary?` and `stableKey`.
- Pass `ChatDependencies` explicitly through initializers, not by reading an environment value inside `init`.
- Migrate internal `Task { ... }` blocks to stored `Task?` properties with `cancel()` hooks.
- Decide and implement the polling ownership policy from §5.6. If background completion should survive dismissal, wire `ActiveChatSessionManager.startTracking` from `handleDisappear()`.
- Remove view-side `Task { await viewModel.sendMessage() }` wrappers where the VM owns the task.

**Definition of done:** `await viewModel.sendMessage()` semantics are either truly awaited or replaced with a non-async `sendMessage()` that explicitly owns a task. Dismissing a polling chat follows the documented policy. Existing `ChatSessionViewModelTests` are updated.
**Risk:** Medium. This changes lifecycle semantics.
**Rollback:** Revert PR.

### Phase 4 — Observation migration *(~0.5 day, 1 PR)*
**Goal:** Narrow invalidation after the model and lifecycle have stabilized.

- Convert `ChatSessionViewModel` from `ObservableObject` to `@Observable`. Drop `@Published`.
- In `ChatSessionView`: `@StateObject` -> `@State`.
- Mark services, timers, tasks, cancellables, formatters, and pending-send maps with `@ObservationIgnored`.
- Pass the observable VM explicitly to child views instead of making descendants observe broad global state.

**Definition of done:** Compiles with `-warnings-as-errors`. Previews render. Signpost trace shows equal-or-fewer relevant `View.body` evaluations on the send + poll flow.
**Risk:** Low-medium. Observation semantics are different; separating this PR makes regressions easier to isolate.
**Rollback:** Revert PR.

### Phase 5 — Native scroll *(~1 day, 1 PR)*
**Goal:** Remove the three competing scroll systems.

- Replace `ScrollViewReader` + `.scrollPosition(id:)` + `ChatScrollStateStore` with iOS 18's `ScrollPosition`, `.defaultScrollAnchor(.bottom)`, and one `isNearBottom` value from `onScrollGeometryChange`.
- Delete `Shared/ChatScrollStateStore.swift`.
- Delete `@State` flags: `scrolledMessageId`, `storedScrollState`, `hasRestoredScroll`, `isAtBottom`, `followLatest`.
- Delete `scrollToBottom`, `updateIsAtBottom`, `restoreScrollPositionIfNeeded`, `persistScrollPosition`, `messageScrollId`, `storedMessageId`, `lastRenderableAnchorId`, `thinkingIndicatorScrollId`.
- Move the thinking indicator from a sibling row to an overlay with bottom content margin.
- Delete the four `onChange` handlers on `ChatSessionView.swift:615-637`.
- Delete the `onAppear`/`onDisappear` scroll-persistence block (`ChatSessionView.swift:638-647`).
- Add a "Jump to latest" pill when `!isNearBottom` and newer content arrives.

**Definition of done:** Scroll passes all 16 flows in §9. User-scroll-up during an in-flight assistant response does not auto-scroll back. New assistant message while user is at bottom pins them to bottom. Keyboard appearance does not force a jump when the user is reading older messages.
**Risk:** Medium. Users will feel any scroll regression immediately.
**Rollback:** Revert PR. Restore `ChatScrollStateStore.swift`.

### Phase 6 — View decomposition *(~1 day, 1 PR)*
**Goal:** Make `ChatSessionView.swift` readable and isolate invalidation.

Extract to `Views/Chat/`:
- `ChatMessageList.swift` — the scroll view, owns `ScrollPosition`.
- `ChatComposerDock.swift` — text field, mic, send, council/context action buttons, recording banner.
- `CouncilBranchTabs.swift` — horizontal carousel with per-candidate status (ties into Phase 8).
- `AssistantMessageBubble.swift` and `UserMessageBubble.swift` — split `MessageBubble` so the branching is removed.
- `ChatEmptyState.swift`, `ChatErrorBanner.swift`, `ChatThinkingOverlay.swift`, `ArticlePreviewCard.swift`.
- `MessageRow.swift` — dispatches to the right bubble type based on `message.role` and `message.displayType`.
- `ChatSessionToolbarContent.swift` — title, article-open affordance, history button, and provider menu.

Extract to `Views/Components/`:
- `ChatShareSheet.swift` — share-sheet adapter and payload.
- `SelectableText.swift` and `DigDeeperTextView.swift` — UIKit selectable text bridge and custom menu action, moved without behavior changes.

Move root previews to `Views/Chat/ChatSessionViewPreviews.swift`.

Other cleanups in the same PR:
- `AssistantFeedOptionActionModel` is owned once above row rendering in both `ChatMessageList` and `QuickMicOverlay`, then passed into feed option rows.
- Per-row `@StateObject var feedOptionActionModel = AssistantFeedOptionActionModel()` ownership has been removed from message bubbles.
- Move expensive display formatting, especially `ChatMessage.formattedTime`, out of hot row render paths by caching parsed dates or formatting in a lightweight presenter. Complete: timestamp parsing/display now uses cached formatter instances.
- Replace the root-level `if/else` in `messageListView` (`ChatSessionView.swift:516-589`) with a stable base view + overlay for loading, inline row for empty state.

**Definition of done:** `ChatSessionView.swift` under 300 lines. `body` under one screen. No computed `some View` over ~30 lines remains.
**Risk:** Low. Pure extraction — the unit tests and previews from Phases 0-2 catch regressions.
**Rollback:** Revert PR.

### Phase 7 — Lifecycle reset & navigation cleanup *(~0.5 day, 1 PR)*
**Goal:** No stale polling after logout. One navigation entry point.

- Add `NotificationCenter.default` `.authDidLogOut` notification. Posted by `AuthenticationService` on logout and on a refresh that fails terminally.
- `ActiveChatSessionManager` observes `.authDidLogOut` and clears state + cancels polling tasks.
- Add `ActiveChatSessionManager.reset()` and fix `hasActiveSession(forContentId:)`.
- `ChatSessionView.task(id:)` calls `ActiveChatSessionManager.shared.stopTracking(sessionId:)` on start.
- Make `ChatNavigationCoordinator` the documented exclusive external-entry coordinator. It is already used; this phase removes the remaining nested `ChatSessionView(sessionId:)` destination and any direct external routing that bypasses it.

**Definition of done:** Manual test — logout with a polling chat in-flight; no crash, no stale notification. Share Extension hand-off opens the chat with pending message visible. Deep link routing verified.
**Risk:** Low.
**Rollback:** Revert PR.

### Phase 8 — Council hardening *(~0.5 day, 1 PR)*
**Goal:** No silent failures, no stuck branch switch.

- `CouncilCandidateCard` renders `processing` / `failed` / `success` states using `candidate.status`. Failed state shows a retry affordance.
- Branch select: single-flight via `selectCouncilTask`. Cancel previous before starting. Preserve previous `activeChildSessionId` on error.
- Add a 10s deadline timer on `selectingCouncilChildSessionId` that surfaces cancel UI.
- `canStartCouncil` moves to `ChatSessionSummary` as a computed property.

**Definition of done:** Flow tests E, E′ (double-tap), council-with-failed-voice preview renders correctly.
**Risk:** Low.
**Rollback:** Revert PR.

### Phase 9 — Voice dictation hardening *(~1 day, 1 PR)*
**Goal:** Audio session always released, interruptions handled, no hangs.

- `AudioRecordingSessionLease` helper (§5.5).
- `AVAudioSession.interruptionNotification` + `routeChangeNotification` observers.
- URLSession timeouts or explicit task-group deadline on `OpenAIService.transcribeAudio`. New `VoiceDictationError.transcriptionTimedOut` case with distinct UX.
- Keep `VoiceDictationService.shared` app-owned, but remove feature state from the singleton by clearing callbacks in `reset()`.
- `ChatSessionView.onDisappear` calls `viewModel.resetVoice()` -> `transcriptionService.reset()`.
- Deactivate the audio session immediately after recorder stop and before transcription upload.
- Composer preserves cursor at end of text after voice transcript application.
- Add a haptic on record start and on stop.

**Definition of done:** Flows F, G, H in §9 pass on device where possible. External-audio test confirms other audio resumes after recording stops.
**Risk:** Medium — audio APIs have subtle platform behavior. The interruption path is hard to test without a real device.
**Rollback:** Revert PR. Audio sessions will once again leak; no data loss.

### Phase 10 — Keyboard behavior *(~0.25 day, 1 PR)*
**Goal:** Standard iOS keyboard behavior; no custom coordination.

- Rely on `.contentMargins(.bottom, 8, for: .scrollContent)` and SwiftUI's default keyboard avoidance.
- After send, keep focus (don't resign). This matches Messages.
- Remove any leftover keyboard-specific `onChange`.

**Definition of done:** Flow L passes.
**Risk:** Trivial.
**Rollback:** Revert PR.

### Phase 11 — Test matrix & verification *(~0.5 day, 1 PR)*
**Goal:** Lock in the new behavior.

- Unit tests for `ChatTimelineReconciler` and VM lifecycle transitions.
- `#Preview` coverage of every state (already added in Phase 0, verified and expanded).
- A checklist doc (`30-verification.md` in this folder) walks through the 16 flows in §9 and records pass/fail.
- Add `ChatTimelineReconcilerTests.swift` with cases for: initial load, send -> poll -> detail reconcile, status assistant -> detail assistant identity stability, council-start reconciliation with candidate ordering, mid-poll duplicate suppression, rapid sends, failed send retry row, logout during poll.

**Definition of done:** `30-verification.md` exists and records pass/fail/not-run status for all flows. Test suite passes in CI. Manual-only flows are tracked until they can be run on simulator/device.
**Risk:** Trivial.
**Rollback:** n/a — test-only.

### Phase 12 — (Deferred) Streaming readiness
**Goal:** Structure the VM so a future SSE streaming switch is a single method change.

- Complete for this initiative: `ChatSessionViewModel` documents the future `apply(streamChunk:)` seam that the reconciliation design supports.
- Do **not** ship streaming in this initiative.
- Out of scope otherwise.

---

## 7. Non-goals

Deliberately excluded to keep scope tight and ship incrementally:
- **Streaming assistant responses** (SSE / chunked token delivery). Backend is polling-based; changing it is out of scope.
- **Offline queueing** of outbound messages. Sends require network.
- **Pagination of message history**. 500 messages in memory has been fine in practice; LazyVStack handles rendering.
- **Changing the council backend contract**. All changes are client-side rendering and state.
- **Redesigning the composer or any typography**. Visual changes are explicitly out of scope.
- **Rewriting `SelectableText` / `DigDeeperTextView`**. They are stable; they'll move into their own files during Phase 6 but behavior is unchanged.
- **Live-tab audio session or TTS changes**. Only dictation is touched.

---

## 8. Anti-patterns this initiative removes

Each line below is a concrete, locatable anti-pattern. Reviewers can verify removal.

| File | Lines | Anti-pattern |
| --- | --- | --- |
| `ChatSessionView.swift` | 258-310 | Three initializers (`session:`, `route:`, `sessionId:`) with inconsistent seeding logic |
| `ChatSessionView.swift` | 248-256 | `@State` flags for manual scroll coordination (`scrolledMessageId`, `storedScrollState`, `hasRestoredScroll`, `isAtBottom`, `followLatest`, `resetsScrollStateOnOpen`) |
| `ChatSessionView.swift` | 312-326 | String-encoded scroll anchor ids (`"__thinking__\|…"`, `messageScrollId`) |
| `ChatSessionView.swift` | 590, 595 | Two separate `ForEach` blocks over partitioned message arrays |
| `ChatSessionView.swift` | 600-608 | Thinking indicator as a sibling row with its own scroll id |
| `ChatSessionView.swift` | 615-637 | Four competing `onChange` handlers mutating scroll state |
| `ChatSessionView.swift` | 638-647 | `onAppear`/`onDisappear` scroll-state save-load cycle |
| `ChatSessionView.swift` | 733-781 | Manual `scrollTo` + restore logic with latching `hasRestoredScroll` |
| `ChatSessionView.swift` | 516-589 | Root-level `if/else` branch swap between loading/empty/error/content states |
| `ChatSessionView.swift` | 1113 | Per-bubble `@StateObject var feedOptionActionModel = AssistantFeedOptionActionModel()` |
| `ChatSessionViewModel.swift` | 14-15 | `ObservableObject` on iOS 18.5+ (Observation API available) |
| `ChatSessionViewModel.swift` | 17-18, 142-143 | Dual message partition `transcriptMessages` + `activeTurnMessages` |
| `ChatSessionViewModel.swift` | 43-80 | Two initializers with overlapping seeding |
| `ChatSessionViewModel.swift` | 219, 244 | Detached `Task { … }` inside an `async` method returning before work completes |
| `ChatSessionViewModel.swift` | 255 | Server echo `activeTurnMessages = [response.userMessage]` replaces seeded placeholder instead of reconciling |
| `ChatSessionViewModel.swift` | 313-316 | `seedInitialPendingMessageIfNeeded` called from `loadSession` — tangled seeding timing |
| `ChatSessionViewModel.swift` | 325-349 | `applyDetail` partitions messages by processing status, moving rows between arrays |
| `ChatSessionViewModel.swift` | 395-413 | `selectCouncilBranch` single-flight via a mutable published property with no task cancellation |
| `Models/ChatMessage.swift` | 254-261 | `scrollIdentity` derived-string identity |
| `Shared/ChatScrollStateStore.swift` | all | UserDefaults-backed scroll persistence (delete) |
| `Services/VoiceDictationService.swift` | 52 | Singleton currently owns feature callbacks and app microphone state together |
| `Services/VoiceDictationService.swift` | 133-139 | `AVAudioSession.setActive(true)` with no matching `setActive(false)` on any path |
| `Services/VoiceDictationService.swift` | — | No `AVAudioSession.interruptionNotification` observer |
| `Services/VoiceDictationService.swift` | 350-367 | No URLSession timeout on transcription |
| `Services/ActiveChatSessionManager.swift` | 52, 120 | Singleton with no logout reset; `hasActiveSession(forContentId:)` checks dictionaries by the wrong key |
| `Services/ChatNavigationCoordinator.swift` | all | Global pending route exists but is undocumented as the exclusive external-entry route |

---

## 9. Manual verification test matrix

Every PR in Phases 3-10 is blocked on the flows it touches passing. Phase 11 runs the full matrix.

| # | Flow | Passes when |
| --- | --- | --- |
| A | Fresh chat from Share Extension with pending first message | Pending bubble appears immediately at bottom, assistant replaces cleanly, no scroll stutter. |
| B | Fresh chat from "Dig Deeper" on an article; VM auto-sends topic | Topic bubble appears, assistant replies, scroll stays at bottom. |
| C | Re-enter an existing chat mid-polling | Chat opens at bottom, in-flight message visible with processing indicator, completion replaces in place with no jump. |
| D | Rapid send: three messages in three seconds | All three user bubbles appear in order; each assistant reply replaces its placeholder in place; list identity stable. |
| E | Start council (3 voices) | Thinking overlay "Gathering council perspectives" appears, card renders with 3 tabs, first tab active. |
| E′ | Double-tap two different council branches while first is selecting | First request cancels; second request lands; no stuck spinner; active id == second tap. |
| F | Dictate → silence auto-stop → edit transcript → send | Audio session released (verify by playing music in another app after recording), composer shows transcript with cursor at end. |
| G | Dictate → receive a phone call | Recording ends with a banner "Recording paused (interruption)"; mic button re-enabled. |
| H | Dictate → background the app → foreground | Recording state correctly reflects reality (either continued or stopped with clear UX); no orphan audio session. |
| I | Navigate away from a chat during polling | No crash; behavior matches the selected lifecycle policy. If manager-owned background polling is chosen, `ActiveChatSessionManager` continues and re-entering shows completed state. |
| J | Logout while a message is polling | All polling tasks cancelled; no notification fires after logout; `activeSessions` cleared. |
| K | Airplane mode during send | Clear error banner, user bubble retained with retry affordance (or at least no silent swallow). |
| L | Keyboard up, assistant response arrives | Composer stays above keyboard; new message scrolls into view *only if* user was at bottom. |
| M | Long transcript (50+ messages), scroll up, new message arrives | Scroll position preserved; no auto-scroll jump. "Jump to latest" pill optionally appears. |
| N | Share a message via context menu | Share sheet presents; dismiss returns to chat with no scroll change. |
| O | Switch provider mid-session | Toolbar menu updates; next message uses new provider; no session state corruption. |
| P | Deep-link into a chat from outside the app | Chat opens with correct session id via `ChatNavigationCoordinator`; pending message (if any) visible. |

---

## 10. Instrumentation plan

**Signpost subsystem:** `com.newsly.chat`
**Category:** `perf`

| Signpost | Type | Notes |
| --- | --- | --- |
| `load-session` | interval | Implemented in `ChatSessionViewModel.loadSession()` |
| `send-message` | interval | Implemented in `ChatSessionViewModel.sendMessage(text:)` |
| `poll-cycle` | interval | Implemented around `pollUntilComplete(messageId:)` |
| `reconcile-detail` | interval | Implemented around `ChatTimelineReconciler.reconcile(...)` |
| `timeline-id-mismatch` | event | Not currently emitted; reconciler tests cover the known status/detail identity mismatch path. Add only if a trace-backed mismatch remains after this refactor. |
| `start-council` | interval | Implemented in `ChatSessionViewModel.startCouncil(message:)` |
| `select-council-branch` | interval | Implemented around the branch-selection task |
| `apply-voice-transcript` | interval | Implemented in `ChatSessionViewModel.applyVoiceTranscript(_:)` |
| `audio-session-activate` | event | |
| `audio-session-deactivate` | event | Paired with activate; any orphan activate is an alert-level bug |

**What we measure on the Phase 0 baseline trace:**
1. View body count during a 60-second session with one send + poll.
2. `reconcile-detail` duration on a 50-message session.
3. Time from `start-council` to first candidate render.
4. `audio-session-activate` vs `audio-session-deactivate` count parity (currently: activate without deactivate).

---

## 11. File-by-file change summary

| File | Change |
| --- | --- |
| `Models/ChatMessage.swift` | Add `: Equatable`. Delete `scrollIdentity` after timeline ids are adopted. `formattedTime` now delegates to cached timestamp formatters. |
| `Models/ChatTimelineItem.swift` *(new)* | `ChatTimelineID`, `ChatTimelineItem`, stable server/local identity derivation. |
| `Models/ChatSessionRoute.swift` | Extend with `session: ChatSessionSummary?` and `stableKey`. Make it the sole `ChatSessionView` entry. |
| `Models/ChatSessionSummary.swift` | Add `canStartCouncil: Bool` computed property. |
| `ViewModels/ChatSessionViewModel.swift` | Single `timeline` array, pure reconciler call, VM-owned `Task?` action lifecycle, explicit polling ownership including disappear handoff, `@Observable` migration, and Phase 0 signpost intervals. |
| `ViewModels/ChatTimelineReconciler.swift` *(new)* | Pure timeline reconciliation from detail/status/pending-send state. |
| `Views/ChatSessionView.swift` | Single init, `@State`-owned `@Observable` VM, route ownership, lifecycle hooks, and named orchestration callbacks. Strict Phase 6 decomposition complete at 280 lines. |
| `ContentView.swift` | Chat navigation destination now keys `ChatSessionView` by `route.stableKey`, so pending-message and pending-council routes do not reuse stale chat state for the same session id. |
| `Views/Chat/ChatSessionToolbarContent.swift` *(new)* | Implemented. Navigation title, article-open affordance, history button, and provider menu extracted from the root chat view. |
| `Views/Chat/ChatMessageList.swift` *(new)* | Implemented. Scroll view, `ScrollPosition`, bottom-follow state, empty/loading/error states, failed-send retry rows, jump-to-latest, and thinking overlay. |
| `Views/Chat/ChatComposerDock.swift` *(new)* | Implemented. Text field + mic + send + context/council action buttons. |
| `Views/Chat/CouncilBranchTabs.swift` *(new)* | Implemented. Horizontal tab carousel with per-candidate selection and timeout-cancel state. |
| `Views/Chat/CouncilCandidatesBubble.swift` *(new)* | Implemented. Active council candidate rendering with processing/failed/default states. |
| `Views/Chat/ChatActivityViews.swift` *(new)* | Implemented. Thinking bubble and initial suggestions loading state. |
| `Views/Chat/ChatErrorBanner.swift` *(new)* | Implemented. Inline chat error banner with council setup affordance. |
| `Views/Chat/MessageRow.swift` *(new)* | Implemented. Row-level wrapper for bubble dispatch plus failed-send retry rendering. |
| `Views/Chat/MessageBubble.swift` *(new)* | Implemented. Dispatcher for process summary, user, and assistant bubbles. |
| `Views/Chat/AssistantMessageBubble.swift`, `Views/Chat/UserMessageBubble.swift` *(new)* | Implemented. Role-specific bubble layouts; assistant rendering includes council and feed options. |
| `Views/Chat/AssistantFeedOptionsSection.swift` *(new)* | Implemented. Assistant feed option rendering and shared subscribe action model. |
| `Views/Components/QuickMicOverlay.swift` | Owns one `AssistantFeedOptionActionModel` above quick-mic message rows and passes it into feed option rendering. |
| `Views/Chat/ChatSecondaryPanel.swift` *(new)* | Implemented. Article/context and active council branch panel. |
| `Views/Chat/ChatEmptyState.swift`, `Views/Chat/ArticlePreviewCard.swift` *(new)* | Implemented. Empty chat and article preview states. |
| `Views/Chat/ChatPreviewFixtures.swift` *(new)* | Implemented. DEBUG-only reusable chat preview fixtures and fake feed-subscription action service. |
| `Views/Chat/ChatSessionViewPreviews.swift` *(new)* | Implemented. Root chat preview moved out of `ChatSessionView.swift`. |
| `Views/Components/ChatShareSheet.swift` *(new)* | Implemented. Share payload and `UIActivityViewController` adapter moved out of the root chat view. |
| `Views/Components/SelectableText.swift`, `Views/Components/DigDeeperTextView.swift` *(new)* | Implemented. Selectable UIKit text bridge and custom dig-deeper menu action moved out of the root chat view without behavior changes. |
| `Shared/ChatScrollStateStore.swift` | **Delete.** |
| `Services/ChatService.swift` | Adds concrete `retryCouncilBranch` client call while preserving existing send/poll behavior. |
| `app/services/council_chat.py` | Council start preserves partial branch failures as failed candidates; per-voice retry reruns one hidden child branch and refreshes parent council metadata. |
| `app/routers/api/chat.py`, `app/models/api/chat.py` | Add `POST /sessions/{session_id}/council/retry`, `CouncilRetryRequest`, and stable `ChatMessageDto.display_key` timeline identity. |
| `Services/APIEndpoints.swift`, `Services/ChatService.swift`, `Models/ChatSessionDetail.swift` | Add iOS endpoint, request DTO, service protocol method, and concrete client call for council branch retry. |
| `Services/ActiveChatSessionManager.swift` | Observe `.authDidLogOut`. Add `reset()`. Fix `hasActiveSession(forContentId:)`. Own background polling after view-dismiss handoff and avoid duplicate pollers on repeated tracking. |
| `Services/ChatNavigationCoordinator.swift` | Document and enforce as the sole external chat route sink. |
| `Services/VoiceDictationService.swift` | `AudioRecordingSessionLease`, interruption and route-change observers, callback cleanup, audio deactivation before upload, timeout mapping, start/stop haptics, and audio-session signpost parity events. Singleton remains app-owned. |
| `Services/SpeechTranscribing.swift` | Ensure `reset()` clears callbacks and service-owned state. |
| `Services/OpenAIService.swift` | `transcribeAudio` gains a deadline/timeout path with sensible default and timeout-specific error mapping. |
| `Services/AuthenticationService.swift` | Post `.authDidLogOut` notification. |
| `App/ChatDependencies.swift` *(new)* | Explicit service bundle passed through view/VM initializers: `chatService`, `transcriptionService`, `activeSessionManager`. |
| `newslyTests/ChatTimelineReconcilerTests.swift` *(new)* | Unit tests per §6 Phase 11. |

---

## 12. Open questions

1. **Backend `display_key`:** resolved. The backend now exposes a stable timeline key and iOS uses it when present.
2. **Polling ownership on disappear:** resolved in code. `handleDisappear()` now hands content-backed in-flight processing to `ActiveChatSessionManager`; manual Flow I remains to validate the user-facing navigation-away behavior end to end.
3. **"Jump to latest" pill:** resolved. The pill shipped with the native-scroll implementation and is covered by the long-transcript Maestro flow.
4. **Per-voice retry for failed council candidates:** resolved in this implementation with `POST /api/content/chat/sessions/{session_id}/council/retry`, which reruns one hidden child branch and refreshes the parent council row.
5. **Transcription deadline:** resolved for this initiative. `VoiceDictationService` applies a 60-second task-group deadline around backend transcription and maps timeout to `VoiceDictationError.transcriptionTimedOut`.
6. **Feed option action ownership:** resolved. `AssistantFeedOptionActionModel` remains the shared action model, owned once above row rendering in chat and Quick Mic.

---

## 13. Appendix: diagnostic highlights (for reviewers)

The following code excerpts are the ones reviewers should look at to confirm the diagnosis before approving the plan.

**Dual message arrays:**
```swift
// ChatSessionViewModel.swift:17-18
@Published private(set) var transcriptMessages: [ChatMessage] = []
@Published private(set) var activeTurnMessages: [ChatMessage] = []

// ChatSessionViewModel.swift:142-143
var allMessages: [ChatMessage] {
    transcriptMessages + activeTurnMessages
}
```

**Two separate `ForEach` blocks:**
```swift
// ChatSessionView.swift:590, 595
ForEach(viewModel.transcriptMessages, id: \.scrollIdentity) { … }
ForEach(viewModel.activeTurnMessages, id: \.scrollIdentity) { … }
```

**Detached task inside `async` method:**
```swift
// ChatSessionViewModel.swift:233-263
func sendMessage(text overrideText: String? = nil) async {
    // …
    Task {                         // ← detaches
        defer { isSending = false; stopThinkingTimer() }
        do {
            let response = try await chatService.sendMessageAsync(…)
            activeTurnMessages = [response.userMessage]
            _ = try await pollUntilComplete(messageId: response.messageId)
            try await refreshTranscriptAfterPolling()
        } catch { … }
    }
}                                   // ← function returns here, caller's await unblocks
```

**Audio session not deactivated:**
```swift
// VoiceDictationService.swift:132-139
try audioSession.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
try audioSession.setActive(true)
// … no matching setActive(false) anywhere in the file
```

**Scroll feedback loop:**
```swift
// ChatSessionView.swift:614-637 (excerpt)
.scrollPosition(id: $scrolledMessageId, anchor: .bottom)
.onChange(of: scrolledMessageId) { _, newId in
    updateIsAtBottom(anchorId: newId)       // writes followLatest
    persistScrollPosition(anchorId: newId)  // writes UserDefaults
}
.onChange(of: viewModel.allMessages.count) { _, _ in
    restoreScrollPositionIfNeeded(proxy: proxy)
    if followLatest { scrollToBottom(proxy: proxy, animated: true) } // writes scrolledMessageId
}
```

**Dangling thinking anchor:**
```swift
// ChatSessionView.swift:312-314, 339-347
private var thinkingIndicatorScrollId: String { "__thinking__|\(viewModel.sessionId)" }

private var lastRenderableAnchorId: String? {
    if viewModel.isSending { return thinkingIndicatorScrollId }   // ← anchor exists
    if let last = viewModel.allMessages.last { return messageScrollId(for: last) }
    return nil
}
// When isSending flips false, the row with thinkingIndicatorScrollId is no longer rendered,
// but scrolledMessageId may still point at it until the next onChange.
```
