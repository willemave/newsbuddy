# client/newsly/ShareExtension/

Source folder: `client/newsly/ShareExtension`

## Purpose
Share extension target that receives shared URLs from iOS, reads shared auth state, and forwards submissions into the backend pipeline.

## Runtime behavior
- Turns iOS share-sheet invocations into authenticated `POST /api/content/submit` requests.
- Relies on shared container/keychain state to reuse app authentication and configuration from the main app target.
- Includes the storyboard/resource metadata needed for the extension UI lifecycle.

## Inventory scope
- Recursive file inventory for `client/newsly/ShareExtension`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `client/newsly/ShareExtension/Base.lproj/MainInterface.storyboard` | n/a | Supporting module or configuration file. |
| `client/newsly/ShareExtension/Info.plist` | n/a | Supporting module or configuration file. |
| `client/newsly/ShareExtension/ShareExtension.entitlements` | n/a | Supporting module or configuration file. |
| `client/newsly/ShareExtension/ShareViewController.swift` | `class ShareViewController`, `enum ShareError`, `handleOptionTapped`, `handleSubmitTapped` | Types: `class ShareViewController`, `enum ShareError`. Functions: `handleOptionTapped`, `handleSubmitTapped` |
