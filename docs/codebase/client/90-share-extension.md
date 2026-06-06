# client/newsly/ShareExtension/

Source folder: `client/newsly/ShareExtension`

## Purpose
iOS share extension target that receives shared URLs/text from other apps and forwards user-selected actions to the backend using shared app authentication state.

## Runtime behavior
- `ShareViewController.swift` supports multiple modes: `addContent`, `createLearningDeck`, `addLinks`, `addFeed`, and `chat`.
- Content submissions post to `/api/content/submit` with flags such as crawl links, subscribe-to-feed, title/platform/content-type, or instruction-style link handling.
- Learning Deck creation posts to `/api/learning/decks`.
- Feed mode supports feed subscription behavior; chat mode hands shared material into the app chat flow.
- The extension reads auth/shared state through app group/keychain configuration and must stay aligned with app entitlements.

## Important files
| File | Purpose |
|---|---|
| `ShareViewController.swift` | Share extension controller, mode routing, backend submission calls, and UI state. |
| `Info.plist` | Extension metadata and activation rules. |
| `ShareExtension.entitlements` | App-group/keychain entitlements. |
| `Base.lproj/MainInterface.storyboard` | Extension storyboard entrypoint. |

## Integration points
- Backend submission routes live under `/api/content/submit` and `/api/learning/decks`.
- URL routing behavior is covered by `ShareURLRoutingTests.swift`.
