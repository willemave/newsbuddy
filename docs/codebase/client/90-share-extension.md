# client/newsly/ShareExtension/

Source folder: `client/newsly/ShareExtension`

## Purpose
iOS share extension target that receives shared URLs/text from other apps and forwards user-selected actions to the backend using shared app authentication state.

## Runtime behavior
- `ShareViewController.swift` presents four outcome-based actions: Add to Briefing, Add to Knowledge, Create Deck, and Chat.
- All four actions post to `/api/share-actions`; the backend owns URL classification, feed discovery, canonicalization, and asynchronous processing.
- Add to Briefing sends `add_to_briefing`, which either subscribes to a continuing source or ingests an individual item. Add to Knowledge sends the compatible `bookmark_only` mode so the item is saved and marked read.
- Create Deck sends `presentation`; Chat sends `chat` with a required first message. Legacy modes remain backend-only for older clients and queued tasks.
- The controller exposes stable `share.*` accessibility identifiers, explains missing-link input, and keeps transient failures recoverable through retry or an explicit open-app action for expired authentication.
- The extension reads auth/shared state through app group/keychain configuration and must stay aligned with app entitlements.
- Because the extension is a separate UIKit target, shared UIKit styling lives in `newsly/Shared/ShareExtensionStyle.swift`; it resolves the accent from `ReaderPalette.brandPrimary`, which is compiled into both targets.

## Important files
| File | Purpose |
|---|---|
| `ShareViewController.swift` | Share extension controller, mode routing, backend submission calls, and UI state. |
| `../newsly/Shared/ShareExtensionStyle.swift` | Shared UIKit color and typography constants compiled into both app and extension targets. |
| `../newsly/Shared/ReaderPalette.swift` | Shared semantic color palette, including the extension's brand accent. |
| `Info.plist` | Extension metadata and activation rules. |
| `ShareExtension.entitlements` | App-group/keychain entitlements. |
| `Base.lproj/MainInterface.storyboard` | Extension storyboard entrypoint. |

## Integration points
- Backend submission and status routes live under `/api/share-actions`.
- URL routing behavior is covered by `ShareURLRoutingTests.swift`.
