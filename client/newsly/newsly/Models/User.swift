//
//  User.swift
//  newsly
//
//  Created by Assistant on 10/25/25.
//

import Foundation

/// User account model matching backend UserResponse schema
struct User: Codable, Identifiable, Equatable {
    let id: Int
    let appleId: String
    let email: String
    let fullName: String?
    let twitterUsername: String?
    let newsDigestPreferencePrompt: String
    let newsDigestTimezone: String
    let newsDigestIntervalHours: Int
    let hasXBookmarkSync: Bool
    let isAdmin: Bool
    let isActive: Bool
    let hasCompletedOnboarding: Bool
    let hasCompletedNewUserTutorial: Bool
    let hasCompletedLiveVoiceOnboarding: Bool
    let createdAt: Date
    let updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case id
        case appleId = "apple_id"
        case email
        case fullName = "full_name"
        case twitterUsername = "twitter_username"
        case newsDigestPreferencePrompt = "news_digest_preference_prompt"
        case newsDigestTimezone = "news_digest_timezone"
        case newsDigestIntervalHours = "news_digest_interval_hours"
        case hasXBookmarkSync = "has_x_bookmark_sync"
        case isAdmin = "is_admin"
        case isActive = "is_active"
        case hasCompletedOnboarding = "has_completed_onboarding"
        case hasCompletedNewUserTutorial = "has_completed_new_user_tutorial"
        case hasCompletedLiveVoiceOnboarding = "has_completed_live_voice_onboarding"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    init(
        id: Int,
        appleId: String,
        email: String,
        fullName: String?,
        twitterUsername: String?,
        newsDigestPreferencePrompt: String,
        newsDigestTimezone: String,
        newsDigestIntervalHours: Int = 6,
        hasXBookmarkSync: Bool,
        isAdmin: Bool,
        isActive: Bool,
        hasCompletedOnboarding: Bool,
        hasCompletedNewUserTutorial: Bool,
        hasCompletedLiveVoiceOnboarding: Bool,
        createdAt: Date,
        updatedAt: Date
    ) {
        self.id = id
        self.appleId = appleId
        self.email = email
        self.fullName = fullName
        self.twitterUsername = twitterUsername
        self.newsDigestPreferencePrompt = newsDigestPreferencePrompt
        self.newsDigestTimezone = newsDigestTimezone
        self.newsDigestIntervalHours = newsDigestIntervalHours
        self.hasXBookmarkSync = hasXBookmarkSync
        self.isAdmin = isAdmin
        self.isActive = isActive
        self.hasCompletedOnboarding = hasCompletedOnboarding
        self.hasCompletedNewUserTutorial = hasCompletedNewUserTutorial
        self.hasCompletedLiveVoiceOnboarding = hasCompletedLiveVoiceOnboarding
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(Int.self, forKey: .id)
        appleId = try container.decode(String.self, forKey: .appleId)
        email = try container.decode(String.self, forKey: .email)
        fullName = try container.decodeIfPresent(String.self, forKey: .fullName)
        twitterUsername = try container.decodeIfPresent(String.self, forKey: .twitterUsername)
        newsDigestPreferencePrompt =
            try container.decodeIfPresent(String.self, forKey: .newsDigestPreferencePrompt) ?? ""
        newsDigestTimezone = try container.decodeIfPresent(String.self, forKey: .newsDigestTimezone) ?? "UTC"
        newsDigestIntervalHours = try container.decodeIfPresent(Int.self, forKey: .newsDigestIntervalHours) ?? 6
        hasXBookmarkSync = try container.decodeIfPresent(Bool.self, forKey: .hasXBookmarkSync) ?? false
        isAdmin = try container.decode(Bool.self, forKey: .isAdmin)
        isActive = try container.decode(Bool.self, forKey: .isActive)
        hasCompletedOnboarding = try container.decodeIfPresent(Bool.self, forKey: .hasCompletedOnboarding) ?? true
        hasCompletedNewUserTutorial = try container.decode(Bool.self, forKey: .hasCompletedNewUserTutorial)
        hasCompletedLiveVoiceOnboarding = try container.decodeIfPresent(
            Bool.self,
            forKey: .hasCompletedLiveVoiceOnboarding
        ) ?? false
        createdAt = try container.decode(Date.self, forKey: .createdAt)
        updatedAt = try container.decode(Date.self, forKey: .updatedAt)
    }
}

/// Token response from authentication endpoints
struct TokenResponse: Codable {
    let accessToken: String
    let refreshToken: String
    let tokenType: String
    let user: User
    let isNewUser: Bool

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case tokenType = "token_type"
        case user
        case isNewUser = "is_new_user"
    }
}

struct AuthSession: Equatable {
    let user: User
    let isNewUser: Bool
}

/// Request for token refresh
struct RefreshTokenRequest: Codable {
    let refreshToken: String

    enum CodingKeys: String, CodingKey {
        case refreshToken = "refresh_token"
    }
}

/// Response for token refresh (with token rotation)
struct AccessTokenResponse: Codable {
    let accessToken: String
    let refreshToken: String
    let tokenType: String

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case tokenType = "token_type"
    }
}

struct UpdateUserProfileRequest: Codable {
    let fullName: String?
    let twitterUsername: String?
    let newsDigestPreferencePrompt: String?
    let newsDigestTimezone: String?
    let newsDigestIntervalHours: Int?

    enum CodingKeys: String, CodingKey {
        case fullName = "full_name"
        case twitterUsername = "twitter_username"
        case newsDigestPreferencePrompt = "news_digest_preference_prompt"
        case newsDigestTimezone = "news_digest_timezone"
        case newsDigestIntervalHours = "news_digest_interval_hours"
    }
}
