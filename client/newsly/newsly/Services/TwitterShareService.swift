//
//  TwitterShareService.swift
//  newsly
//
//  Service for sharing tweets to Twitter/X.
//

import Foundation
import UIKit

/// Service for sharing content to Twitter/X via deep links or web fallback.
@MainActor
final class TwitterShareService {
    static let shared = TwitterShareService()

    private static let twitterAppSchemes: [String] = [
        "twitter://post?message=",
        "x://post?message="
    ]

    private static let twitterWebURL = "https://twitter.com/intent/tweet?text="

    private init() {}

    /// Share a tweet to Twitter/X.
    /// Tries native app first, falls back to Safari.
    func share(tweet: String, completion: ((Bool) -> Void)? = nil) {
        // Use a restricted character set that encodes &, =, +, #, and other query-special chars.
        // .urlQueryAllowed keeps these unencoded, which breaks URLs when tweet text contains them.
        var allowedCharacters = CharacterSet.alphanumerics
        allowedCharacters.insert(charactersIn: "-._~")

        guard let encodedTweet = tweet.addingPercentEncoding(withAllowedCharacters: allowedCharacters) else {
            completion?(false)
            return
        }

        // Try Twitter/X app schemes first
        for scheme in Self.twitterAppSchemes {
            if let appURL = URL(string: scheme + encodedTweet),
               UIApplication.shared.canOpenURL(appURL) {
                UIApplication.shared.open(appURL, options: [:]) { success in
                    completion?(success)
                }
                return
            }
        }

        // Fall back to web intent
        if let webURL = URL(string: Self.twitterWebURL + encodedTweet) {
            UIApplication.shared.open(webURL, options: [:]) { success in
                completion?(success)
            }
            return
        }

        completion?(false)
    }

}
