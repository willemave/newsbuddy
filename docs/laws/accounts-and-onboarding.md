# Accounts and Onboarding Laws

A1. Private product data belongs to the authenticated user. Matching numeric IDs never grant cross-user access, and device caches are cleared when the account changes.

A2. Apple sign-in is the production identity boundary; debug users are development-only.

A3. Refresh tokens rotate once. A consumed token cannot mint another session; for a short bounded window, repeating the same recorded rotation attempt may retrieve the one already-issued replacement.

A4. Account deletion requires fresh authorization and deactivates the account only after durable cleanup work has been accepted.

A5. API keys, provider tokens, and user-managed model keys are encrypted, revocable secrets. They are never returned in full or written to logs.

A6. Onboarding suggestions remain proposals until the user confirms them.

A7. Completing onboarding validates and saves the full selection atomically, then queues the initial source work.

A8. Onboarding failures never leave half-applied selections or report false completion. Progress from independent successful sources remains durable.

A9. Onboarding leads into the real Briefing, where usable sources and categories may appear incrementally.

A10. Product preferences travel with the account, while purely visual preferences may remain on the device.

A11. A cached authenticated shell may be shown only for the identity bound to the stored credentials. Transient validation failure preserves that matching shell; only definitive credential rejection transitions it to signed out.

A12. A credential publication binds one complete access/refresh pair to one user and generation. Legacy loose credentials must be server-validated before they can establish cached identity, a stale plaintext mirror cannot resurrect credentials after the bound record exists, and an interrupted one-leg publication cannot be treated as or overwritten by an atomic pair.
