# Knowledge and Learning Laws

K1. Knowledge is the authenticated user's durable saved library. Saving twice creates one save, and removing it leaves read state, chats, and source content intact.

K2. Saved items remain searchable, pageable, and readable after they leave the inbox. The CLI favorites list returns the most recently saved articles in save-time order; other content types do not count toward its requested limit. It returns fewer only when the saved library is exhausted, and failed retrieval never reports a successful partial list.

K3. A normal save leaves read state unchanged. Actions that save and mark read must say so explicitly.

K4. Knowledge preserves source provenance and remains authoritative over best-effort Markdown or other exported copies.

K5. Knowledge combines saved items, chats, Learning Decks, and narrations in one reverse-chronological stream. A saved source and its chats and Learning Decks occupy one source row, retaining the source title and access to every related chat and deck. Its activity time is the latest of the user's save and related chat/deck activity, never the source's publication or ingestion time. Chats and decks without a saved source in the loaded library remain independently visible. Entries stay compact and keep titles to one trailing-truncated line. Failure in one source cannot erase the others.

K6. A Learning Deck keeps one explicit source identity, its notes, and its attribution. Source text is read within a 20 MiB budget; an oversized source fails explicitly and is never silently truncated. A URL submitted for a deck becomes saved source material and stays out of unread Briefing. A source saved only for a deck is prepared as readable text without an editorial summary or content artwork; a later ordinary submission may promote the same source to full content processing.

K7. At most one deck generation is active per user, and rerunning the same source reuses its deck identity.

K8. A deck becomes viewable after a successful artifact exists. A failed rerun cannot invalidate an earlier successful artifact.

K9. Private deck links expire. Public sharing is explicit and remains available only while enabled.

K10. Deleting a deck cancels active generation, revokes access, and removes artifacts owned by that deck. Durable deletion succeeds even if immediate storage cleanup fails; recurring cleanup retries the artifacts without protecting them merely because the deleted deck still lists their keys.

K11. Deck chat keeps the deck visible, uses the deck's identity, and remains secondary to the reading surface. Its bottom bar and expanded composer both offer voice input. The composer and its actions remain reachable above the keyboard; collapsing chat dismisses text input. Dictated prompts follow the same durable send path as typed prompts, preserve any typed draft until submission, and recording stops when the reader closes or the app backgrounds.

K12. Learning Deck generation has a default fifteen-minute budget starting after source preparation, shared across automatic retries and publication. A new explicitly requested generation starts a fresh budget. It may iterate within its execution deadline, with no application-level model-request or output-token ceiling. A high tool-call ceiling guards against runaway loops without truncating ordinary deck generation. The execution deadline, artifact size and contract checks, and browser validation remain mandatory safeguards.

K13. Initial Knowledge loading publishes one merged timeline. Fast loads do not flash transient empty, partial-source, or loading states; sustained loads and independent source failures remain visible.

K14. Learning Deck validation renders the same viewer shell clients receive, and the validated HTML is the HTML published to clients. The viewer may configure generated decks but cannot rewrite their authored scripts; external Reveal assets use the supported pinned runtime; detailed validator failures stay internal while users receive a stable recovery message. The generator uses the same canonical validation tool, and infrastructure failures are never misreported as malformed deck content.

K15. A successful Learning Deck may publish a source-specific, deck-cover-style thumbnail with its artifact bundle. Thumbnail generation cannot block an otherwise valid deck, failed reruns preserve the last successful bundle, and Knowledge uses stable placeholder artwork when no thumbnail exists.

K16. Knowledge keeps its published merged timeline during lifecycle revalidation and recoverable connectivity failure. Temporary inactivity alone is not a reload trigger; a true warm resume may revalidate once without exposing partial-source results. A successful Add to Knowledge share invalidates cached library freshness for that account and server, including a return without backgrounding. A failed read cannot consume that invalidation. Backgrounding may cancel lifecycle-owned reads, but it cannot cancel an explicit user refresh that already joined the same work.

K17. While a Knowledge item or one of its source-linked chats or decks is actively preparing, generating, or answering, a loading spinner replaces the source row's leading icon or thumbnail without changing row layout, and the row identifies the active work. Ready, failed, unavailable, and stalled work does not show an active-processing spinner. While Knowledge is visible and active, bounded read-only refreshes discover imported saves and update processing rows without a manual refresh, losing loaded pagination depth, or restarting preparation. Observation stops when the screen is hidden or the app is inactive.

K18. A saved item that is unavailable or stalled can be reprocessed by the user who saved it. Reprocessing restarts the item's full preparation from URL analysis for every content type, never duplicates preparation that is already queued or running, and does not apply to items that are already ready.

K19. A ready saved item offers its deep dives from the Knowledge row without opening it: chat, Learning Deck, and narration on one swipe edge (a full swipe starts or resumes chat), and removal on the other. Chat from a row resumes that source's latest Knowledge chat, if one exists, and otherwise starts one. Deck from a row opens the existing deck or its generation status, if one exists, and otherwise offers creation. A started deck updates its source row immediately; a started narration appears in Knowledge immediately, and the user stays on the list. Once a chat or deck exists for a ready source, its row shows a trailing button for it that resumes the latest chat or opens the latest deck without opening the source; other existing deep dives are marked on the row. Ready rows carry no disclosure chevron. Related chats and decks remain individually accessible and deletable, and a deck can be regenerated from its source row. The first time a ready saved item is visible, the newest one briefly slides open to show these actions; the hint plays once per install.

K20. Assistant screen context uses trimmed text, an `unknown` screen type for blank input, and positive visible source IDs. Primary sources appear first and duplicate sources appear once in the durable snapshot. Context titles remain bounded when continuing a session with a longer display title.

K21. Learning Deck creation identifies its source and accepts optional focus by typing or dictation before explicit creation. It presents no preset focus suggestion buttons. Its controls remain reachable with the keyboard open and at accessibility text sizes; form content cannot overlap the sheet's navigation controls.

K22. Complete private deck drafts survive automatic retries without becoming visible as published decks. Only the current owned generation may save or restore its draft; deletion, cancellation, and lease replacement prevent stale publication. Users can distinguish generation, checking, repair, recovery, and publication. Exhausted status observation stops the spinner and offers a read-only refresh without starting another generation.
