# Accounts and Onboarding Laws

A1. Private product data belongs to the authenticated user. Matching numeric IDs never grant cross-user access, and device caches are cleared when the account changes.

A2. Apple sign-in is the production identity boundary; debug users are development-only.

A3. Refresh tokens rotate once. A consumed token cannot mint another session; for a short bounded window, repeating the same recorded rotation attempt may retrieve the one already-issued replacement.

A4. Account deletion requires fresh authorization and deactivates the account only after durable cleanup work has been accepted.

A5. API keys, provider tokens, and user-managed model keys are encrypted, revocable secrets. They are never returned in full or written to logs.

A6. Personalized onboarding suggestions are server-owned proposals with stable IDs scoped to one authenticated user's discovery run. They remain proposals until that user confirms their IDs.

A7. Personalized completion names the completed discovery run and selected persisted suggestion IDs; it never echoes source URLs, subreddit names, or inferred profile text. The server derives canonical source data, revalidates run ownership and every selected ID in the final transaction, saves the full selection atomically, and queues the initial source work. The explicit non-personalized path has no discovery run and cannot select discovered suggestions.

A8. Onboarding failures never leave half-applied selections or report false completion. Progress from independent successful sources remains durable.

A9. Onboarding leads into the real Briefing, where usable sources and categories may appear incrementally.

A10. Product preferences travel with the account, while purely visual preferences may remain on the device.

A11. A cached authenticated shell may be shown only for the identity bound to the stored credentials. Transient validation failure preserves that matching shell; only definitive credential rejection transitions it to signed out.

A12. A credential publication binds one complete access/refresh pair to one user and generation. Legacy loose credentials must be server-validated before they can establish cached identity, a stale plaintext mirror cannot resurrect credentials after the bound record exists, and an interrupted one-leg publication cannot be treated as or overwritten by an atomic pair.

A13. Completing a discovery run never schedules that discovery again. Later source expansion belongs to the separate durable feed-discovery workflow.

A14. Onboarding guidance never obscures or intercepts actionable controls. The Buddy arrives expanded on the welcome, blinks, and then withdraws to the upper-left corner on its own, where it stays for the rest of the flow; advancing a step never waits on that animation. Loading states use the centered Buddy indicator, and reduced-motion users receive the settled docked presentation without the arrival.

A15. The signed-out landing presents the complete slate ring and Buddy mark as the primary brand image, on transparency so it sits on the surface rather than in a field of its own. The icon's own square field is reserved for places that are showing the app icon as an icon. The standalone Buddy is reserved for in-flow guidance and loading feedback.

A16. The signed-out landing offers sign-in and nothing else. Legal and support destinations, and the disclosure that external AI services process submitted content, live in Settings, which must keep reachable links to the privacy policy, terms and support.
