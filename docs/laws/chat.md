# Chat Laws

CH1. Every chat is a durable, user-owned session that may be ad hoc or explicitly grounded in content, news, a deck, or screen context.

CH2. Accepting a send saves the user message and queues its assistant turn atomically. Once accepted, navigation or backgrounding cannot lose the turn.

CH3. Closed, archived, failed, or otherwise unwritable sessions reject new sends.

CH4. Context resolves through its exact typed identity. Coincidental numeric IDs never change the target.

CH5. Session history is user-scoped and stably paged. Archived sessions stay hidden, and archiving a chat leaves source content and Knowledge saves intact.

CH6. The visible transcript contains user and assistant output without model scaffolding or tool chatter. In-flight assistant text is a durable advisory snapshot under the final row identity until terminal state replaces it.

CH7. Stable timeline identity prevents polling from duplicating or replacing the wrong row. Sends follow the bottom, while unrelated updates preserve the reader's position.

CH8. Cancellation remains distinct from failure, recoverable errors preserve the transcript, and dictated prompts follow the same durable send path as typed prompts.

CH9. Chat follows one conversation with one assistant response per accepted turn. Retired council conversations retain their selected response and visible follow-ups as ordinary chats; alternate branch records remain archived. Shared history and council retry prompts are not duplicated during conversion, and later sends use ordinary chat without expert impersonation.

CH10. Opening global chat dismisses covering presentations and installs one visible route.

CH11. Learning Deck chat stays grounded in the deck and its sources, remains secondary to reading, and searches the web only for an explicit current, external, or verification request.

CH12. A chat turn that invokes no VM tool performs no sandbox operation.

CH13. A task sandbox starts without a mounted user corpus. Knowledge stays authoritative on the host; only explicitly selected, reauthorized, bounded items may be copied into that task workspace. Newsly mutations, product credentials, and vendor credentials remain on the host.

CH14. Sandbox-capable agents expose execute, read, write, exact edit, and list for task-workspace files, plus typed host Knowledge search, read, and bounded-copy tools. Tool progress remains retry-fenced and separate from visible transcript text.

CH15. Every E2B-backed product workflow uses the canonical Newsly agent template. Provider-default and per-workflow template selection are not runtime fallbacks.

CH16. Model history contains newest complete user turns within the remaining request budget. Output, tool schemas, system/context material, and the current prompt are reserved before history; trimming never leaves an orphaned assistant or tool sequence.

CH17. Knowledge lookup in chat is served by a host-side search tool over the user's saved library. Searching knowledge alone never acquires a sandbox.

CH18. Reading a Knowledge reference alone never acquires a sandbox. A chat sandbox is created lazily on the first execution or file-writing tool and is killed when the turn ends.

CH19. A content list requested for a specific show or publication preserves that source identity. Shared guests, topics, or mentions of the requested source do not make an item belong to it. Individual articles and episodes have their own links, and bounded lists disclose their scope.

CH20. New OpenAI chat sessions and missing-model chat fallbacks use GPT-6.1 Sol. Existing explicit model selections remain attached to their sessions; this default does not change summarization or other cheap-model work.

CH21. Content retrieval applies source ownership and read/saved filters before paging. Ambiguous followed-source names are surfaced explicitly. Saved content can be browsed without an exact keyword, empty search results remain empty, and returned counts describe the same accessible set as the page.

CH22. Content-grounded chat titles use the first nonblank summary title, feed-preview title, stored title, or short summary, including an artifact overview. Display titles collapse whitespace and bound long text with an ellipsis. New screen-context snapshots contain source context without an account ID line.

CH23. Dedicated Deep Research is retired. New research uses ordinary chat and its standard tools. Historical research conversations remain readable, while retired-session writes and pending dedicated research attempts cannot start or resume provider work. Retirement markers in the provider, session type, or model identify a read-only conversation before a send is accepted.

CH24. A confirmed chat action remains a completed action if subsequent answer generation fails. The assistant may confirm only committed outcomes; failed or ambiguous commits cannot be reported as successes. Confirmations distinguish a changed topic filter from an unchanged existing follow. Cancellation and lease loss remain distinct from completion. Retrying an action reconciles its current authoritative state without duplicating downstream work.

CH25. Ordinary chat can search and read public pages, resolve named sources, inspect subscriptions, and act on authorized library items through host tools. Bash and filesystem tools execute only in the task sandbox, acquired lazily. Saved Knowledge, inbox content, and News retain explicit search scopes and typed identities.

CH26. Chat has its own bounded execution budget and reserves an opportunity for a final answer. A budget stop preserves confirmed outcomes and exposes unfinished work truthfully. Other workflows retain their own execution and validation policies.
