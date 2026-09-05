# Knowledge and Learning Laws

K1. Knowledge is the authenticated user's durable saved library. Saving twice creates one save, and removing it leaves read state, chats, and source content intact.

K2. Saved items remain searchable, pageable, and readable after they leave the inbox.

K3. A normal save leaves read state unchanged. Actions that save and mark read must say so explicitly.

K4. Knowledge preserves source provenance and remains authoritative over best-effort Markdown or other exported copies.

K5. Knowledge combines saved items, chats, Learning Decks, and narrations in one reverse-chronological stream. Entries stay compact and keep titles to one trailing-truncated line. A saved item's activity time is when that user saved it, not when the source was published or ingested. Failure in one source cannot erase the others.

K6. A Learning Deck keeps one explicit source identity, its notes, and its attribution. A URL submitted for a deck becomes saved source material and stays out of unread Briefing.

K7. At most one deck generation is active per user, and rerunning the same source reuses its deck identity.

K8. A deck becomes viewable after a successful artifact exists. A failed rerun cannot invalidate an earlier successful artifact.

K9. Private deck links expire. Public sharing is explicit and remains available only while enabled.

K10. Deleting a deck cancels active generation, revokes access, and removes artifacts owned by that deck.

K11. Deck chat keeps the deck visible, uses the deck's identity, and remains secondary to the reading surface.

K12. Learning Deck generation may iterate for as many model and tool turns as it needs within its execution deadline, with no application-level model-request or output-token ceiling. It cannot fail solely because it crossed either fixed budget; the execution deadline, tool-call limit, artifact size and contract checks, and browser validation remain mandatory safeguards.

K13. Initial Knowledge loading publishes one merged timeline. Fast loads do not flash transient empty, partial-source, or loading states; sustained loads and independent source failures remain visible.

K14. Learning Deck validation renders the same viewer shell clients receive, and the validated HTML is the HTML published to clients. The viewer may configure generated decks but cannot rewrite their authored scripts; external Reveal assets use the supported pinned runtime; detailed validator failures stay internal while users receive a stable recovery message.

K15. A successful Learning Deck may publish a source-specific, deck-cover-style thumbnail with its artifact bundle. Thumbnail generation cannot block an otherwise valid deck, failed reruns preserve the last successful bundle, and Knowledge uses stable placeholder artwork when no thumbnail exists.

K16. Knowledge keeps its published merged timeline during lifecycle revalidation and recoverable connectivity failure. Temporary inactivity is not a reload trigger; a true warm resume may revalidate once without exposing partial-source results. Backgrounding may cancel lifecycle-owned reads, but it cannot cancel an explicit user refresh that already joined the same work.

K17. While a Knowledge item is actively preparing, generating, or answering, a loading spinner replaces its leading icon or thumbnail without changing row layout. Ready, failed, unavailable, and stalled items do not show an active-processing spinner.
