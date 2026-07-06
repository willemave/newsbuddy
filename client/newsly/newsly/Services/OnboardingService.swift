//
//  OnboardingService.swift
//  newsly
//
//  Created by Assistant on 1/17/26.
//

import Foundation
import os.log

private let onboardingServiceLogger = Logger(subsystem: "com.newsly", category: "OnboardingService")

private func onboardingServiceElapsedMilliseconds(since start: Date) -> Int {
    Int(Date().timeIntervalSince(start) * 1000)
}

final class OnboardingService {
    static let shared = OnboardingService()
    private let client = APIClient.shared

    private init() {}

    func audioDiscover(request: OnboardingAudioDiscoverRequest) async throws -> OnboardingAudioDiscoverResponse {
        let startedAt = Date()
        if let fixtureResponse = OnboardingE2EFixtureStore.shared?.audioDiscoverResponse {
            onboardingServiceLogger.info(
                "Audio discovery fixture response | runId=\(fixtureResponse.runId) elapsedMs=\(onboardingServiceElapsedMilliseconds(since: startedAt))"
            )
            return fixtureResponse
        }
        onboardingServiceLogger.info(
            "Audio discovery request started | transcriptChars=\(request.transcript.count) locale=\(request.locale ?? "nil", privacy: .public)"
        )
        let body = try JSONEncoder().encode(request)
        do {
            let response: OnboardingAudioDiscoverResponse = try await client.request(
                APIEndpoints.onboardingAudioDiscover,
                method: "POST",
                body: body
            )
            onboardingServiceLogger.info(
                "Audio discovery request completed | runId=\(response.runId) status=\(response.runStatus, privacy: .public) laneCount=\(response.lanes.count) elapsedMs=\(onboardingServiceElapsedMilliseconds(since: startedAt))"
            )
            return response
        } catch {
            onboardingServiceLogger.error(
                "Audio discovery request failed | elapsedMs=\(onboardingServiceElapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
            throw error
        }
    }

    func discoveryStatus(runId: Int) async throws -> OnboardingDiscoveryStatusResponse {
        let startedAt = Date()
        if let fixtureResponse = OnboardingE2EFixtureStore.shared?.discoveryStatusResponse {
            return fixtureResponse
        }
        let queryItems = [URLQueryItem(name: "run_id", value: String(runId))]
        let response: OnboardingDiscoveryStatusResponse = try await client.request(
            APIEndpoints.onboardingDiscoveryStatus,
            method: "GET",
            queryItems: queryItems
        )
        onboardingServiceLogger.info(
            "Audio discovery status fetched | runId=\(runId) status=\(response.runStatus, privacy: .public) elapsedMs=\(onboardingServiceElapsedMilliseconds(since: startedAt)) laneCount=\(response.lanes.count)"
        )
        return response
    }

    func complete(request: OnboardingCompleteRequest) async throws -> OnboardingCompleteResponse {
        let body = try JSONEncoder().encode(request)
        return try await client.request(
            APIEndpoints.onboardingComplete,
            method: "POST",
            body: body
        )
    }

    func markTutorialComplete() async throws -> OnboardingTutorialResponse {
        try await client.request(
            APIEndpoints.onboardingTutorialComplete,
            method: "POST"
        )
    }
}
