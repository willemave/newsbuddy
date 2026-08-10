# Chat Laws

CH1. Every chat is a durable, user-owned session that may be ad hoc or explicitly grounded in content, news, a deck, or screen context.

CH2. Accepting a send persists the user message and queues its assistant turn atomically; enqueue failure leaves no zombie message or session.

CH3. Once the server acknowledges a turn, navigation or backgrounding must not lose it; the client reconciles the durable result on return.

CH4. A closed, archived, failed, or otherwise unwritable session rejects new sends instead of creating ambiguous state.

CH5. Context always resolves through the exact typed identity; a coincidental content or news numeric ID never changes the target.

CH6. Session history is user-scoped, stably paged, and hides archived sessions and internal council branches while keeping visible work-in-progress sessions.

CH7. Archiving a council parent archives its children but does not delete source content or Knowledge saves.

CH8. The visible transcript contains meaningful user and assistant output, not model-facing scaffolding or internal tool chatter.

CH9. Timeline identity distinguishes durable display rows from asynchronous backing-message IDs so polling cannot duplicate or replace the wrong row.

CH10. A user send follows the bottom; unrelated incoming updates preserve the reader's position and offer a deliberate jump to the latest message.

CH11. Cancellation is not a chat failure, and recoverable errors preserve the transcript with a clear retry path.

CH12. Typed and dictated prompts enter the same send path and produce the same durable session semantics.

CH13. Council branches run independently, retain failed candidates for inspection, allow targeted retry, and route later sends to the selected branch.

CH14. Opening global chat dismisses any covering detail or Briefing presentation and installs one visible route.
