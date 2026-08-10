# Audio and Voice Laws

AV1. Every narration and generated audio artifact is user-scoped unless the owner explicitly enables public sharing.

AV2. Audio generation is durable asynchronous work; playback and streaming follow the same persisted generation rather than starting hidden duplicate work.

AV3. Audio can use only supported, user-visible sources with enough readable text to narrate.

AV4. Briefing narration reads the complete server-side lens in editorial order and preserves its authored prose instead of asking another model to rewrite it.

AV5. Briefing chapters target a useful listening length without splitting a segment; playback may begin once the first chapter is ready.

AV6. Custom narration uses the selected sources and fails clearly when a source type or body is unsupported.

AV7. Creating audio never marks content read; custom narration applies its explicit playback read policy, while Briefing sources become read only after the full narration finishes.

AV8. Public audio sharing is explicit and revocable, and revocation invalidates the public route.

AV9. User-facing audio failures are sanitized and never expose provider, prompt, filesystem, or credential detail.

AV10. One feature owns microphone capture at a time and releases it on cancellation, interruption, navigation, and every terminal path.

AV11. Manual stop and silence stop converge on at most one transcript and one action; cancellation produces neither.

AV12. Dictation feeds the same validation and action path as typed input, and unavailable voice service always leaves typing usable.
