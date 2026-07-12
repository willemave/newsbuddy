# Briefing Empty State

## Goal

Make an empty Briefing feel like a quiet editorial state rather than a broken or unfinished screen.

## Experience

- Keep the normal Briefing masthead and date.
- Lead with “Your next edition is taking shape.”
- Explain that Newsly needs enough related stories to form a readable category.
- Teach that pulling down checks for a new edition; do not add a refresh button.
- Show refresh progress and refresh failures inline without replacing the entire screen.
- Offer a secondary route to Settings so the reader can manage sources.
- When categories become available, use the existing Briefing transition into the first category.

## States

| State | Status copy |
| --- | --- |
| Idle | Waiting for enough related stories |
| Requesting or waiting | Checking your sources… |
| Failed | Show the refresh failure message |

HTTP `304 Not Modified` is a successful revalidation. It leaves this state visible and must not be presented as an error.

## Scope

This change replaces the generic Briefing `EmptyStateView` only. It does not change the Briefing API, ETag behavior, refresh queue, or first-run Start Here experience.
