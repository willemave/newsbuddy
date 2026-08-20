# Audio and Voice Laws

AV1. Narrations and generated audio belong to one user unless that user explicitly enables public sharing. Revocation closes public access.

AV2. Audio generation is durable asynchronous work. Playback and streaming follow the same persisted generation without starting hidden duplicate work.

AV3. Audio can use only supported, user-visible sources with enough readable text to narrate.

AV4. Briefing narration follows the complete canonical lens in editorial order and preserves its authored prose. Chapters never split a segment, and playback may start when the first chapter is ready.

AV5. Custom narration uses the selected sources and fails clearly when a source or body is unsupported.

AV6. Creating audio never marks content read. Custom narration follows its stated playback policy, while Briefing sources become read only after the full narration finishes.

AV7. User-facing audio failures never expose provider, prompt, filesystem, or credential details.

AV8. One feature owns microphone capture at a time and releases it after cancellation, interruption, navigation, or completion.

AV9. Manual stop and silence stop produce at most one transcript and one action. Cancellation produces neither.

AV10. Dictation follows the same validation and action path as typed input, and typing remains available when voice service fails.
