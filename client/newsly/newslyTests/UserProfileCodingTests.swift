import Foundation
import XCTest
@testable import newsly

final class UserProfileCodingTests: XCTestCase {
    func testUserDecodesXDigestFilterPrompt() throws {
        let json = """
        {
          "id": 1,
          "apple_id": "apple-1",
          "email": "user@example.com",
          "full_name": "Test User",
          "twitter_username": "willem_aw",
          "x_digest_filter_prompt": "Prefer semiconductors and infra updates.",
          "news_digest_timezone": "America/Los_Angeles",
          "news_digest_interval_hours": 6,
          "has_x_bookmark_sync": true,
          "is_admin": false,
          "is_active": true,
          "has_completed_onboarding": true,
          "has_completed_new_user_tutorial": true,
          "has_completed_live_voice_onboarding": false,
          "created_at": "2026-03-26T20:00:00Z",
          "updated_at": "2026-03-26T20:00:00Z"
        }
        """.data(using: .utf8)!

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        let user = try decoder.decode(User.self, from: json)

        XCTAssertEqual(user.xDigestFilterPrompt, "Prefer semiconductors and infra updates.")
    }

    func testUpdateUserProfileRequestEncodesXDigestFilterPrompt() throws {
        let request = UpdateUserProfileRequest(
            fullName: nil,
            twitterUsername: "willem_aw",
            xDigestFilterPrompt: "Keep market structure and product updates.",
            newsDigestTimezone: nil,
            newsDigestIntervalHours: nil
        )

        let data = try JSONEncoder().encode(request)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

        XCTAssertEqual(
            json["x_digest_filter_prompt"] as? String,
            "Keep market structure and product updates."
        )
    }

    func testOnboardingCompleteRequestEncodesXDigestFilterPrompt() throws {
        let request = OnboardingCompleteRequest(
            selectedSources: [],
            selectedSubreddits: [],
            profileSummary: nil,
            inferredTopics: nil,
            twitterUsername: nil,
            xDigestFilterPrompt: "Prefer original reporting and firsthand product notes."
        )

        let data = try JSONEncoder().encode(request)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

        XCTAssertEqual(
            json["x_digest_filter_prompt"] as? String,
            "Prefer original reporting and firsthand product notes."
        )
    }
}
