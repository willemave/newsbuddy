import Foundation
import XCTest
@testable import newsly

final class UserProfileCodingTests: XCTestCase {
    func testUserDecodesProfileWithLegacyFields() throws {
        let json = """
        {
          "id": 1,
          "apple_id": "apple-1",
          "email": "user@example.com",
          "full_name": "Test User",
          "twitter_username": "willem_aw",
          "council_personas": [
            {
              "id": "analyst",
              "display_name": "Analyst",
              "instruction_prompt": "Focus on the core argument.",
              "sort_order": 0
            },
            {
              "id": "skeptic",
              "display_name": "Skeptic",
              "instruction_prompt": "Stress-test assumptions.",
              "sort_order": 1
            },
            {
              "id": "builder",
              "display_name": "Builder",
              "instruction_prompt": "Make it practical.",
              "sort_order": 2
            },
            {
              "id": "historian",
              "display_name": "Historian",
              "instruction_prompt": "Add historical context.",
              "sort_order": 3
            }
          ],
          "has_x_bookmark_sync": true,
          "is_admin": false,
          "is_active": true,
          "has_completed_onboarding": true,
          "has_completed_new_user_tutorial": true,
          "created_at": "2026-03-26T20:00:00Z",
          "updated_at": "2026-03-26T20:00:00Z"
        }
        """.data(using: .utf8)!

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        let user = try decoder.decode(User.self, from: json)

        XCTAssertEqual(user.fullName, "Test User")
        XCTAssertEqual(user.twitterUsername, "willem_aw")
        XCTAssertTrue(user.hasXBookmarkSync)
    }

    func testUpdateUserProfileRequestEncodesReadingExperience() throws {
        let request = APIUpdateUserProfileRequest(readingExperience: .briefing)

        let data = try JSONEncoder().encode(request)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

        XCTAssertEqual(json["reading_experience"] as? String, "briefing")
    }

    func testUpdateUserProfileRequestEncodesTimezoneRevision() throws {
        let request = APIUpdateUserProfileRequest(
            timezone: "America/Los_Angeles",
            timezoneRevision: 7
        )

        let data = try JSONEncoder().encode(request)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

        XCTAssertEqual(json["timezone"] as? String, "America/Los_Angeles")
        XCTAssertEqual(json["timezone_revision"] as? Int, 7)
    }

    func testUserFallsBackToDefaultReadingExperienceWhenMissingFromPayload() throws {
        let json = """
        {
          "id": 1,
          "apple_id": "apple-1",
          "email": "user@example.com",
          "full_name": "Test User",
          "twitter_username": null,
          "has_x_bookmark_sync": false,
          "is_admin": false,
          "is_active": true,
          "has_completed_onboarding": true,
          "has_completed_new_user_tutorial": true,
          "created_at": "2026-03-26T20:00:00Z",
          "updated_at": "2026-03-26T20:00:00Z"
        }
        """.data(using: .utf8)!

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        let user = try decoder.decode(User.self, from: json)

        XCTAssertEqual(user.readingExperience, .briefing)
    }
}
