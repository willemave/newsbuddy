# Chat Laws

CH1. Every chat is a durable, user-owned session that may be ad hoc or explicitly grounded in content, news, a deck, or screen context.

CH2. Accepting a send saves the user message and queues its assistant turn atomically. Once accepted, navigation or backgrounding cannot lose the turn.

CH3. Closed, archived, failed, or otherwise unwritable sessions reject new sends.

CH4. Context resolves through its exact typed identity. Coincidental numeric IDs never change the target.

CH5. Session history is user-scoped and stably paged. Archived sessions and internal council branches stay hidden, and archiving a council leaves source content and Knowledge saves intact.

CH6. The visible transcript contains user and assistant output without model scaffolding or tool chatter. In-flight assistant text is a durable advisory snapshot under the final row identity until terminal state replaces it.

CH7. Stable timeline identity prevents polling from duplicating or replacing the wrong row. Sends follow the bottom, while unrelated updates preserve the reader's position.

CH8. Cancellation remains distinct from failure, recoverable errors preserve the transcript, and dictated prompts follow the same durable send path as typed prompts.

CH9. Council branches run independently, retain failed candidates for inspection, support targeted retry, and route later sends to the selected branch.

CH10. Opening global chat dismisses covering presentations and installs one visible route.

CH11. Learning Deck chat stays grounded in the deck and its sources, remains secondary to reading, and searches the web only for an explicit current, external, or verification request.

CH12. A chat turn that invokes no VM tool performs no sandbox operation.

CH13. The VM sees the user's readable corpus as credential-free files. Newsly mutations, product credentials, and vendor credentials remain on the host.

CH14. VM-capable agents expose the same five execution tools: execute, read, write, exact edit, and list. Tool progress remains retry-fenced and separate from visible transcript text.

CH15. Every E2B-backed product workflow uses the canonical Newsly agent template. Provider-default and per-workflow template selection are not runtime fallbacks.

CH16. Model history contains newest complete user turns within the remaining request budget. Output, tool schemas, system/context material, and the current prompt are reserved before history; trimming never leaves an orphaned assistant or tool sequence.
