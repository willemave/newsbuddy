# Audio and Voice Laws

AV1. Narrations and generated audio belong to one user unless that user explicitly enables public sharing. Revocation closes public access.

AV2. Audio generation is durable asynchronous work. Playback and streaming follow the same persisted generation without starting hidden duplicate work.

AV3. Audio can use only supported, user-visible sources with enough readable text to narrate.

AV4. Briefing narration uses the complete eligible unread source set of the lens selected when Play is requested, in canonical order. Spoken scripts are grounded adaptations of those sources. News chapters never split a segment; article and podcast chapters each cover one source. Playback may start when the first chapter is ready. Browsing another lens does not replace the playing queue, and automatic advance stays within its originating lens. Retry preserves the original edition and chapter identities. After its final chapter finishes, a new Play request waits for pending read tracking before selecting the lens's current unread sources; pause/resume and explicit chapter replay retain the edition.

AV5. Custom narration uses the selected sources and fails clearly when a source or body is unsupported.

AV6. Creating audio never marks content read. Custom narration follows its stated playback policy, while Briefing chapter sources become read when that chapter naturally finishes. News completion covers the complete planned source window, including sources omitted from the spoken script.

AV7. User-facing audio failures never expose provider, prompt, filesystem, or credential details.

AV8. One feature owns microphone capture at a time and releases it after cancellation, interruption, navigation, or completion.

AV9. Manual stop and silence stop produce at most one transcript and one action. Cancellation produces neither.

AV10. Dictation follows the same validation and action path as typed input, and typing remains available when voice service fails.
